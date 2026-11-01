# JailbreakGuard — Module 1

JailbreakGuard is a local dashboard for reviewing text for a few obvious jailbreak and prompt-injection indicators. The React + Vite frontend sends text to a Flask API, which runs the transparent rules in `scanner.py`. Matches are prompts for human review, not proof that text is malicious; no match is not proof that text is safe.

Submitted prompt text and uploaded file contents are processed locally in memory for the current request. Uploads are limited to 10 MB and are not saved; responses contain only filename, detected format, character count, extraction status, and scan findings. The application does not log prompt or extracted content and makes no external API calls. Supported uploads are TXT, Markdown, PDF, DOCX, PNG, JPG, JPEG, PPTX, XLSX, JSON, XML, HTML, HTM, WAV, MP3, M4A, and FLAC. XLSX formulas and macros are not executed. XML is parsed with external entities disabled, and HTML scripts and styles are ignored.

## Requirements

- Python 3.12
- pypdf, python-docx, python-pptx, openpyxl, and defusedxml from requirements.txt
- scikit-learn from requirements.txt for the optional local classifier experiment
- Node.js and npm

## Setup in Windows CMD

Open **two** Command Prompt windows in `D:\JailbreakGuard`. In the first window, set up and start Flask:

```cmd
cd /d D:\JailbreakGuard
.venv\Scripts\activate
python --version
python -m pip install -r requirements.txt
python app.py
```

If `.venv\Scripts\python.exe` reports `No Python at ...Python312\python.exe`, the existing environment is stale. Install Python 3.12 from python.org with **Add Python to PATH** enabled, open a new CMD window, then repair it:

```cmd
cd /d D:\JailbreakGuard
rmdir /s /q .venv
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Only run `rmdir` after confirming the target is exactly `D:\JailbreakGuard\.venv`.

In a second CMD window, install and start the React development server:

```cmd
cd /d D:\JailbreakGuard\frontend
npm install
npm run dev
```

Open `http://localhost:5173` in your browser. Keep both servers running while using the dashboard. Vite forwards `/scan` and `/scan-file` requests to Flask at `http://127.0.0.1:5000`; Flask itself provides the API and does not serve the React page. Stop either server with `Ctrl+C`.

## Run backend tests

In another CMD window (or after stopping a server):

```cmd
cd /d D:\JailbreakGuard
.venv\Scripts\activate
python -m pytest -q
```

## Offline dataset benchmark

The optional evaluator reads the local Parquet dataset with PyArrow and calls the production `scanner.scan_text()` function. The dataset README documents `label` values `0 = benign` and `1 = malicious`; the evaluator checks every selected label and stops with an error on unrecognized values. It also validates the scanner's `level` result (`clear`/`warning`) instead of assuming an output shape. It never uploads text or writes prompt content to the report. Reports contain row counts, label counts, metrics, and category names only.

Install the evaluator dependency and inspect the locally downloaded files first:

```cmd
cd /d D:\JailbreakGuard
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\evaluate_dataset.py --inspect
```

Run a full test split, or use a deterministic sample. The seed controls hash-ranked row selection and the report includes the selected sample limit and seed:

```cmd
.venv\Scripts\python.exe scripts\evaluate_dataset.py --dataset core --split test
.venv\Scripts\python.exe scripts\evaluate_dataset.py --dataset full --split test --max-samples 500 --seed 2026 --output reports\full_test_500.json
.venv\Scripts\python.exe scripts\evaluate_dataset.py --dataset data --split train --max-samples 500 --seed 2026
```

Available configurations are `core` and `full` with `train`, `validation`, and `test` splits, plus `data` with `train`. `--inspect` reports the Parquet schemas, observed label/category distributions, exact duplicate files, and row-level overlap by text hash and `group_id`; it emits no prompt text. In this checkout, core/full validation files and core/full test files have identical SHA-256 hashes, so these pairs are the same partitions and should not be treated as independent evaluations.

The default JSON output is written under `reports/`. Its shape is similar to:

```json
{
  "dataset": "core",
  "split": "test",
  "source_rows": 942,
  "seed": 2026,
  "sample_limit": 500,
  "metrics": {
    "examples": 500,
    "skipped_examples": 0,
    "accuracy": 0.0,
    "precision": 0.0,
    "recall": 0.0,
    "f1": 0.0,
    "false_positive_rate": 0.0,
    "false_negative_rate": 0.0,
    "confusion_matrix": {"tn": 0, "fp": 0, "fn": 0, "tp": 0},
    "label_distribution": {"benign": 0, "attack": 0},
    "per_category": {}
  }
}
```

The numbers above are schema placeholders, not benchmark results. This rule-based benchmark measures performance on one local dataset and does not prove protection against all prompt injections or jailbreaks. The `data` split's metadata is not described in the dataset README; inspect its actual labels with `--inspect` before interpreting its metrics.

### Training error analysis and local model comparison

These development commands only read `core/train` and `core/validation`. The analysis report stores category counts, scanner rule counts, aggregate obfuscation signals, and overlap counts; it never stores prompt text or excerpts. The overlap comparison normalizes text for exact matches and uses 3-word-shingle Jaccard at 0.85 for near matches. In this checkout the observed train/validation overlap was 31 normalized exact row pairs (28 distinct normalized texts), 11 additional near-duplicate pairs, and no shared `group_id`s. Treat the split metrics as correlated evidence; do not include `data/train`, and do not treat `full` validation/test as independent from `core` validation/test.

An optional local TF-IDF + Logistic Regression experiment fits on `core/train`, uses `core/validation` to select the model and threshold, and compares rules-only, ML-only, and OR-hybrid predictions. It excludes normalized exact and near-duplicate training rows found against validation, then fits only on the remaining `core/train` rows. It does not save model artifacts or prompt text. It prefers validation candidates with precision at least 0.90 and false-positive rate at most 0.05; if none qualify, the report marks that constraint as unmet. Validation results are selection evidence and may be optimistic. They do not establish real-world protection, and the classifier remains an experiment rather than a production scanner.

Reproduce the reports in Windows CMD:

```cmd
cd /d D:\JailbreakGuard
.venv\Scripts\python.exe scripts\evaluate_dataset.py --dataset core --split train --output reports\core_train_before.json
.venv\Scripts\python.exe scripts\evaluate_dataset.py --dataset core --split validation --output reports\core_validation_before.json
.venv\Scripts\python.exe scripts\analyze_rules.py --output reports\core_train_error_analysis.json
.venv\Scripts\python.exe scripts\evaluate_models.py --output reports\core_validation_models.json
.venv\Scripts\python.exe -m pytest -q
cd /d D:\JailbreakGuard\frontend
npm.cmd run build
```

## Project files

- `app.py` - Flask APIs at `POST /scan`, `POST /scan-file`, and `POST /scan-audio`.
- `scripts/evaluate_dataset.py` - offline scanner benchmark and dataset overlap inspection.
- `scripts/analyze_rules.py` - prompt-free core train/validation error-signal and overlap analysis.
- `scripts/evaluate_models.py` - isolated local TF-IDF/logistic-regression validation experiment.
- `file_scanner.py` — in-memory file validation and text extraction.
- `scanner.py` — small, readable, rule-based text scanner.
- `tests/` — scanner and Flask API tests.
- `requirements.txt` — Python dependencies.
- `frontend/src/App.jsx` — React dashboard, scan workflow, and in-memory session history.
- `frontend/src/styles.css` — responsive dashboard styling.
- `frontend/vite.config.js` — Vite dev server and Flask API proxy.
- `frontend/package.json` — frontend dependencies and commands.

The older `templates/` and `static/` files are retained as reference files from the original Flask interface; Flask no longer serves them.

## Optional image OCR setup

Image OCR uses local Tesseract through Pillow and pytesseract; no model or content is sent to an external service. Install the Python packages with `python -m pip install Pillow pytesseract`. Install Tesseract OCR for Windows separately and ensure `tesseract.exe` is on PATH. Restart Flask afterward. If OCR dependencies or the executable are missing, image scans return an explicit setup error. Image-only PDFs are reported as requiring OCR; PDF OCR is not included in this phase.


## Local audio scanning (Module 5)

`POST /scan-audio` accepts WAV, MP3, M4A, and FLAC uploads up to 10 MB. Audio must also be no longer than 5 minutes by default (`JAILBREAKGUARD_MAX_AUDIO_SECONDS` can change this limit). The service validates the extension and container signature, decodes audio locally, then scans the transcript with the existing `scanner.py` rules. Transcripts and audio are held in memory for the request and are not returned or logged. This is not a forensic audio authentication system.

The optional audio path uses `faster-whisper` with PyAV. It is loaded lazily on the first audio request, not when Flask starts. The default model is `tiny`, CPU device, and `int8` compute to avoid requiring a GPU. The tiny model is approximately 75 MB of weights and needs additional working RAM; larger models require more download space and memory. Dependencies can be installed in Windows CMD with:

```cmd
cd /d D:\JailbreakGuard
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

On first use, faster-whisper may download the selected model weights from Hugging Face. User audio remains local, but downloading weights requires network access. To use a previously downloaded model directory and stay offline at inference time, set these variables before starting Flask:

```cmd
set JAILBREAKGUARD_WHISPER_MODEL=D:\Models\faster-whisper-tiny
set JAILBREAKGUARD_WHISPER_DEVICE=cpu
set JAILBREAKGUARD_WHISPER_COMPUTE_TYPE=int8
.venv\Scripts\python.exe app.py
```

`JAILBREAKGUARD_WHISPER_MODEL` also accepts faster-whisper model names such as `tiny`, `base`, or `small`; non-local model names can trigger a weight download on first request. The upload byte limit can be changed with `JAILBREAKGUARD_MAX_UPLOAD_BYTES` (default 10485760), the duration limit with `JAILBREAKGUARD_MAX_AUDIO_SECONDS` (default 300), and the normalized peak level treated as silence with `JAILBREAKGUARD_AUDIO_SILENCE_THRESHOLD` (default 0.0001; valid range 0 to 1). Missing dependencies, unavailable weights, decoding errors, silence, and empty transcripts return explicit errors/statuses and are never reported as clean scans. Do not describe this setup as offline until dependencies and model weights have been installed locally and verified without network access.

Audio scanning is tested with mocked decoder/model outputs; no real audio sample or Whisper model has been used to verify transcription accuracy. Before relying on it, install the runtime, scan a short known WAV/MP3 sample, compare the returned indicators against its transcript yourself, and test with network access disabled after caching the model if offline use is required.
