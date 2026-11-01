"""Lazy, local audio transcription with bounded in-memory processing."""

from io import BytesIO
import os
import threading


class AudioScanError(Exception):
    def __init__(self, message, status, http_status=422):
        super().__init__(message)
        self.status = status
        self.http_status = http_status


AUDIO_FORMATS = {".wav": "wav", ".mp3": "mp3", ".m4a": "m4a", ".flac": "flac"}
_model = None
_model_lock = threading.Lock()
_transcription_lock = threading.Lock()


def validate_audio(filename, data):
    """Check allowlisted extension and a matching container/audio signature."""
    from pathlib import Path

    ext = Path(filename or "").suffix.lower()
    if ext not in AUDIO_FORMATS:
        raise AudioScanError("Unsupported audio format. Upload WAV, MP3, M4A, or FLAC.", "unsupported_format", 415)
    if not data:
        raise AudioScanError("The uploaded audio file is empty.", "invalid_audio")
    valid = {
        "wav": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE",
        "flac": data.startswith(b"fLaC"),
        "mp3": data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0),
        "m4a": len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {b"M4A ", b"isom", b"mp41", b"mp42", b"qt  "},
    }[AUDIO_FORMATS[ext]]
    if not valid:
        raise AudioScanError("Audio content does not match the filename extension.", "invalid_audio")
    return AUDIO_FORMATS[ext]


def _decode_audio(data, max_seconds):
    """Decode locally after checking container duration; returns 16 kHz samples."""
    try:
        import av
        from faster_whisper.audio import decode_audio
    except ImportError as error:
        raise AudioScanError(
            "Audio scanning needs faster-whisper and its PyAV runtime. Install project requirements.",
            "dependency_unavailable", 503,
        ) from error

    try:
        with av.open(BytesIO(data), mode="r") as container:
            streams = [stream for stream in container.streams if stream.type == "audio"]
            if not streams:
                raise AudioScanError("No audio stream was found in this file.", "invalid_audio")
            duration = container.duration / av.time_base if container.duration is not None else None
            if duration is None and streams[0].duration is not None:
                duration = float(streams[0].duration * streams[0].time_base)
            if duration is not None and duration > max_seconds:
                raise AudioScanError(f"Audio exceeds the {max_seconds}-second processing limit.", "too_long", 413)
        samples = decode_audio(BytesIO(data), sampling_rate=16000)
    except AudioScanError:
        raise
    except Exception:
        raise AudioScanError("The audio file is malformed, unsupported, or could not be decoded.", "invalid_audio") from None

    if len(samples) == 0:
        raise AudioScanError("No audio samples were decoded.", "invalid_audio")
    if len(samples) > max_seconds * 16000:
        raise AudioScanError(f"Audio exceeds the {max_seconds}-second processing limit.", "too_long", 413)
    return samples


def _reject_silence(samples):
    """Reject digital silence, while allowing very quiet speech by default."""
    try:
        threshold = float(os.environ.get("JAILBREAKGUARD_AUDIO_SILENCE_THRESHOLD", "0.0001"))
    except ValueError:
        raise AudioScanError("JAILBREAKGUARD_AUDIO_SILENCE_THRESHOLD must be a number from 0 to 1.", "configuration_error", 500) from None
    if not 0 <= threshold <= 1:
        raise AudioScanError("JAILBREAKGUARD_AUDIO_SILENCE_THRESHOLD must be between 0 and 1.", "configuration_error", 500)
    peak = max(abs(float(sample)) for sample in samples)
    if peak <= threshold:
        raise AudioScanError("The audio contains silence and cannot be transcribed.", "silent_audio")


def _load_model():
    """Create one shared model only on first use; never at app startup."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as error:
                raise AudioScanError(
                    "Audio scanning needs faster-whisper. Install project requirements.",
                    "dependency_unavailable", 503,
                ) from error
            model_name = os.environ.get("JAILBREAKGUARD_WHISPER_MODEL", "tiny")
            device = os.environ.get("JAILBREAKGUARD_WHISPER_DEVICE", "cpu")
            compute_type = os.environ.get("JAILBREAKGUARD_WHISPER_COMPUTE_TYPE", "int8" if device == "cpu" else "float16")
            try:
                _model = WhisperModel(model_name, device=device, compute_type=compute_type)
            except Exception as error:
                raise AudioScanError(
                    "The local Whisper model is unavailable. Check the model name and runtime; model weights may need to be downloaded on first use.",
                    "model_unavailable", 503,
                ) from error
    return _model


def transcribe_audio(filename, data, max_seconds=300):
    """Transcribe locally and return (format, transcript); never stores either."""
    audio_format = validate_audio(filename, data)
    samples = _decode_audio(data, max_seconds)
    _reject_silence(samples)
    model = _load_model()
    try:
        with _transcription_lock:
            segments, _info = model.transcribe(samples, beam_size=1)
            transcript = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    except Exception:
        raise AudioScanError("Local audio transcription failed. Try another supported audio file.", "transcription_failed") from None
    if not transcript:
        raise AudioScanError("Audio decoded successfully, but no transcript was produced.", "empty_transcript")
    return audio_format, transcript
