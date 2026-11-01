from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename
import os

from audio_transcriber import AudioScanError, transcribe_audio
from file_scanner import FileScanError, extract_file
from scanner import scan_text


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("JAILBREAKGUARD_MAX_UPLOAD_BYTES", 10 * 1024 * 1024))
app.config["AUDIO_MAX_SECONDS"] = int(os.environ.get("JAILBREAKGUARD_MAX_AUDIO_SECONDS", 300))


@app.errorhandler(HTTPException)
def handle_http_error(error):
    """Keep Flask's client and routing errors in the API's JSON format."""
    messages = {
        400: "Request body must contain valid JSON.",
        404: "API endpoint not found.",
        405: "HTTP method is not allowed for this endpoint.",
        415: "Content-Type must be application/json.",
        413: "Upload exceeds the configured size limit.",
    }
    payload = {"error": messages.get(error.code, "The request could not be completed.")}
    if error.code == 413:
        if request.path == "/scan-audio":
            payload["transcription_status"] = "too_large"
            payload["transcript_character_count"] = 0
        else:
            payload["extraction_status"] = "too_large"
    return jsonify(payload), error.code


@app.errorhandler(Exception)
def handle_unexpected_error(_error):
    """Return a generic JSON error without including or logging prompt text."""
    return jsonify({"error": "The scanner could not complete this request."}), 500


@app.post("/scan")
def scan():
    if not request.is_json:
        return jsonify({"error": "Content-Type must be application/json."}), 415

    payload = request.get_json()
    if not isinstance(payload, dict):
        return jsonify({"error": "Send a JSON object containing prompt text."}), 400
    text = payload.get("text", "")

    try:
        result = scan_text(text)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify(result)


@app.post("/scan-file")
def scan_file():
    if not request.content_type or not request.content_type.lower().startswith("multipart/form-data"):
        return jsonify({"error": "Content-Type must be multipart/form-data."}), 415
    uploaded = request.files.get("file")
    if uploaded is None:
        return jsonify({"error": "Choose a file to scan."}), 400
    # Read at most the configured maximum plus one byte; never write the upload
    # to a persistent or temporary filesystem location.
    data = uploaded.stream.read(app.config["MAX_CONTENT_LENGTH"] + 1)
    if len(data) > app.config["MAX_CONTENT_LENGTH"]:
        return jsonify({"error": "Upload exceeds the configured size limit.", "extraction_status": "too_large"}), 413
    filename = secure_filename(uploaded.filename or "") or "upload"
    try:
        detected_format, extracted = extract_file(filename, data)
    except FileScanError as error:
        return jsonify({
            "filename": filename,
            "format": secure_filename(filename.rsplit(".", 1)[-1]).lower() if "." in filename else "unknown",
            "extraction_status": error.status,
            "character_count": 0,
            "error": str(error),
        }), error.http_status

    if not extracted.strip():
        return jsonify({"filename": filename, "format": detected_format,
                        "extraction_status": "empty_extraction", "character_count": 0,
                        "error": "No text was found in this file."}), 422
    result = scan_text(extracted)
    return jsonify({"filename": filename, "format": detected_format,
                    "extraction_status": "success", "character_count": len(extracted),
                    **result})


@app.post("/scan-audio")
def scan_audio():
    is_multipart = request.mimetype == "multipart/form-data"
    # Werkzeug's test client can send data={} without a Content-Length header.
    # Detect that empty body for unsupported request types before returning 415.
    # Keep multipart parsing on the existing bounded upload path.
    if request.content_length == 0 or (
        not is_multipart
        and request.content_length is None
        and not request.get_data(cache=True)
    ):
        return jsonify({"error": "Choose an audio file to scan."}), 400
    if not is_multipart:
        return jsonify({"error": "Content-Type must be multipart/form-data."}), 415
    uploaded = request.files.get("file")
    if uploaded is None:
        return jsonify({"error": "Choose an audio file to scan."}), 400
    data = uploaded.stream.read(app.config["MAX_CONTENT_LENGTH"] + 1)
    if len(data) > app.config["MAX_CONTENT_LENGTH"]:
        return jsonify({"error": "File exceeds the configured upload size limit.", "transcription_status": "too_large", "transcript_character_count": 0}), 413

    filename = secure_filename(uploaded.filename or "") or "upload"
    try:
        audio_format, transcript = transcribe_audio(filename, data, app.config["AUDIO_MAX_SECONDS"])
    except AudioScanError as error:
        return jsonify({
            "filename": filename,
            "format": filename.rsplit(".", 1)[-1].lower() if "." in filename else "unknown",
            "transcription_status": error.status,
            "transcript_character_count": 0,
            "error": str(error),
        }), error.http_status

    try:
        result = scan_text(transcript)
    except ValueError:
        return jsonify({
            "filename": filename, "format": audio_format,
            "transcription_status": "empty_transcript", "transcript_character_count": 0,
            "error": "Audio decoded successfully, but no usable transcript was produced.",
        }), 422
    count = len(transcript)
    return jsonify({
        "filename": filename,
        "format": audio_format,
        "transcription_status": "success",
        "transcript_character_count": count,
        "character_count": count,
        **result,
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
