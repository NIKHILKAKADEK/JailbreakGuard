"""Local, in-memory extraction for supported upload formats."""

from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import json
import warnings
import zipfile


class FileScanError(Exception):
    def __init__(self, message, status="extraction_failed", http_status=400):
        super().__init__(message)
        self.status = status
        self.http_status = http_status


ALLOWED_FORMATS = {
    ".txt": "txt", ".md": "md", ".pdf": "pdf", ".docx": "docx",
    ".png": "png", ".jpg": "jpg", ".jpeg": "jpeg",
    ".pptx": "pptx", ".xlsx": "xlsx", ".json": "json",
    ".xml": "xml", ".html": "html", ".htm": "htm",
}
MAX_ARCHIVE_UNCOMPRESSED = 100 * 1024 * 1024


def _validate_signature(fmt, data):
    signatures = {
        "pdf": data.startswith(b"%PDF-"),
        "png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": data.startswith(b"\xff\xd8\xff"),
        "jpeg": data.startswith(b"\xff\xd8\xff"),
    }
    if fmt in ("docx", "pptx", "xlsx"):
        required = {
            "docx": {"[Content_Types].xml", "word/document.xml"},
            "pptx": {"[Content_Types].xml", "ppt/presentation.xml"},
            "xlsx": {"[Content_Types].xml", "xl/workbook.xml"},
        }[fmt]
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                infos = archive.infolist()
                if sum(item.file_size for item in infos) > MAX_ARCHIVE_UNCOMPRESSED:
                    raise FileScanError("The document archive exceeds the safe extraction limit.")
                signatures[fmt] = required.issubset(archive.namelist()) and archive.testzip() is None
        except FileScanError:
            raise
        except Exception:
            signatures[fmt] = False
    if fmt in signatures and not signatures[fmt]:
        raise FileScanError("The file content does not match its extension or is malformed.")


class _ReadableHTML(HTMLParser):
    """Collect visible text while dropping script/style/noscript/template bodies."""
    IGNORED = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.IGNORED:
            self.hidden_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.IGNORED and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data):
        if not self.hidden_depth:
            self.parts.append(data)


def _extract_zip_text(fmt, data):
    try:
        if fmt == "pptx":
            from pptx import Presentation
            deck = Presentation(BytesIO(data))
            return "\n".join(
                shape.text for slide in deck.slides for shape in slide.shapes
                if getattr(shape, "has_text_frame", False) and shape.text.strip()
            )
        from openpyxl import load_workbook
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True, keep_links=False)
        try:
            values = []
            for sheet in workbook.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    values.extend(str(value) for value in row if value is not None)
            return "\n".join(values)
        finally:
            workbook.close()
    except ImportError as error:
        dependency = "python-pptx" if fmt == "pptx" else "openpyxl"
        raise FileScanError(f"{fmt.upper()} scanning needs {dependency}. Install the project requirements.", "dependency_unavailable", 503) from error
    except FileScanError:
        raise
    except Exception:
        raise FileScanError(f"The {fmt.upper()} file is malformed or could not be read.") from None


def extract_file(filename, data):
    """Extract a supported file in memory and return (format, extracted text)."""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_FORMATS:
        raise FileScanError("Unsupported file type. Upload TXT, MD, PDF, DOCX, PNG, JPG, JPEG, PPTX, XLSX, JSON, XML, HTML, or HTM.", "unsupported_format", 415)
    fmt = ALLOWED_FORMATS[ext]
    if not data:
        raise FileScanError("The uploaded file is empty.", "extraction_failed")
    _validate_signature(fmt, data)

    if fmt in ("txt", "md"):
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise FileScanError("Text files must use UTF-8 encoding.") from None
        return fmt, content

    if fmt == "json":
        try:
            document = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise FileScanError("The JSON file is malformed or is not valid UTF-8.") from None
        # JSON values are converted to readable text without serializing content
        # back to the caller. Keys as well as scalar values are scanned.
        def flatten(value):
            if isinstance(value, dict):
                return [part for key, item in value.items() for part in (str(key), *flatten(item))]
            if isinstance(value, list):
                return [part for item in value for part in flatten(item)]
            return [str(value)] if value is not None else []
        return fmt, "\n".join(flatten(document))

    if fmt == "xml":
        try:
            from defusedxml import ElementTree as SafeET
            root = SafeET.fromstring(data.decode("utf-8-sig"))
            return fmt, "\n".join(value.strip() for value in root.itertext() if value.strip())
        except ImportError as error:
            raise FileScanError("XML scanning needs defusedxml. Install the project requirements.", "dependency_unavailable", 503) from error
        except Exception:
            raise FileScanError("The XML file is malformed or contains unsafe entity declarations.") from None

    if fmt in ("html", "htm"):
        try:
            parser = _ReadableHTML()
            parser.feed(data.decode("utf-8-sig"))
            parser.close()
            return fmt, "\n".join(part.strip() for part in parser.parts if part.strip())
        except Exception:
            raise FileScanError("The HTML file is malformed or is not valid UTF-8.") from None

    if fmt in ("pptx", "xlsx"):
        _validate_signature(fmt, data)
        return fmt, _extract_zip_text(fmt, data)

    if fmt == "pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise FileScanError("Encrypted PDFs cannot be scanned. Remove the password and try again.")
            content = "\n".join(page.extract_text() or "" for page in reader.pages)
        except FileScanError:
            raise
        except ImportError as error:
            raise FileScanError("PDF scanning needs pypdf. Install the project requirements.", "dependency_unavailable", 503) from error
        except Exception:
            raise FileScanError("The PDF is malformed or could not be read.") from None
        if not content.strip():
            raise FileScanError("No selectable text was found in this PDF. OCR is required for image-only PDFs.", "ocr_required", 422)
        return fmt, content

    if fmt == "docx":
        try:
            from docx import Document
            document = Document(BytesIO(data))
            content = "\n".join(paragraph.text for paragraph in document.paragraphs)
            content += "\n" + "\n".join(cell.text for table in document.tables for row in table.rows for cell in row.cells)
        except ImportError as error:
            raise FileScanError("DOCX scanning needs python-docx. Install the project requirements.", "dependency_unavailable", 503) from error
        except Exception:
            raise FileScanError("The DOCX file is malformed or could not be read.") from None
        return fmt, content

    try:
        from PIL import Image
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        image = Image.open(BytesIO(data))
        image.verify()
        image = Image.open(BytesIO(data))
    except ImportError as error:
        raise FileScanError("Image scanning needs Pillow and Tesseract OCR. Install the optional OCR dependencies.", "ocr_unavailable", 503) from error
    except Exception:
        raise FileScanError("The image is malformed or could not be read.") from None
    try:
        import pytesseract
        content = pytesseract.image_to_string(image)
    except ImportError as error:
        raise FileScanError("Image scanning needs pytesseract and the Tesseract executable. See README setup instructions.", "ocr_unavailable", 503) from error
    except Exception:
        raise FileScanError("Tesseract OCR is unavailable or failed. Install Tesseract and try again.") from None
    if not content.strip():
        raise FileScanError("OCR completed but found no readable text in the image.", "empty_extraction", 422)
    return fmt, content
