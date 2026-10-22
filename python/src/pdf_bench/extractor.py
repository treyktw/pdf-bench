"""PDF text extraction using PyMuPDF, pdfplumber, and optional OCR fallback."""

from __future__ import annotations

import importlib
import io
from contextlib import contextmanager
from collections.abc import Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pdfplumber

pytesseract_module: ModuleType | None = None
pil_image_module: ModuleType | None = None

pdf_lib: Any
try:
    pdf_lib = importlib.import_module("pymupdf")
except ImportError:  # pragma: no cover - legacy import fallback
    pdf_lib = importlib.import_module("fitz")

try:
    import pytesseract as _pytesseract_module
    from PIL import Image as _pil_image_module

    pytesseract_module = _pytesseract_module
    pil_image_module = _pil_image_module
    OCR_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    OCR_AVAILABLE = False

pytesseract: ModuleType | None = pytesseract_module
PILImage: ModuleType | None = pil_image_module


@contextmanager
def start_span(_name: str, **_kwargs: object):
    """No-op span context for the standalone benchmark extract."""

    yield None


def set_span_attributes(_span: object, _attributes: dict[str, object]) -> None:
    """No-op metric hook for the standalone benchmark extract."""


class ExtractionPermanentError(ValueError):
    """Permanent extraction failure for malformed or unsupported PDFs."""


class OCRTimeoutError(ExtractionPermanentError):
    """OCR exceeded its hard timeout and should not be retried blindly."""


@dataclass(slots=True)
class ExtractionResult:
    text: str
    tables: list[list[list[str]]]
    ocr_used: bool


def _stringify_fragment(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _normalize_table(raw_table: object) -> list[list[str]]:
    if not isinstance(raw_table, Sequence) or isinstance(raw_table, (str, bytes)):
        return []

    normalized_rows: list[list[str]] = []
    for raw_row in raw_table:
        if not isinstance(raw_row, Sequence) or isinstance(raw_row, (str, bytes)):
            continue
        normalized_rows.append([_stringify_fragment(cell) for cell in raw_row])
    return normalized_rows


def _open_pdf_document(data: bytes) -> Any:
    try:
        return pdf_lib.open(stream=data, filetype="pdf")
    except Exception as exc:  # pragma: no cover - backend-specific parse errors
        raise ExtractionPermanentError("Invalid or unreadable PDF document.") from exc


def validate_pdf_page_cap(data: bytes, max_pdf_pages: int) -> int:
    if max_pdf_pages <= 0:
        return 0

    doc = _open_pdf_document(data)
    try:
        page_count = len(doc)
    finally:
        doc.close()

    if page_count > max_pdf_pages:
        raise ExtractionPermanentError(
            f"PDF exceeds max allowed page count of {max_pdf_pages} pages."
        )
    return page_count


def extract_text_pymupdf(data: bytes) -> str:
    with start_span("pdf.extract.text", tracer_name=__name__) as span:
        doc = _open_pdf_document(data)

        parts: list[str] = []
        try:
            set_span_attributes(span, {"pdf.page_count": len(doc)})
            for page_index in range(len(doc)):
                page = doc.load_page(page_index)
                page_text = page.get_text()
                parts.append(_stringify_fragment(page_text))
        finally:
            doc.close()
        text = "\n\n".join(parts)
        set_span_attributes(span, {"pdf.text_chars": len(text)})
        return text


def extract_tables_pdfplumber(data: bytes) -> list[list[list[str]]]:
    with start_span("pdf.extract.tables", tracer_name=__name__) as span:
        tables: list[list[list[str]]] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            set_span_attributes(span, {"pdf.page_count": len(pdf.pages)})
            for page in pdf.pages:
                raw_page_tables = page.extract_tables()
                if not raw_page_tables:
                    continue
                for raw_table in raw_page_tables:
                    normalized_table = _normalize_table(raw_table)
                    if normalized_table:
                        tables.append(normalized_table)
        set_span_attributes(span, {"pdf.table_count": len(tables)})
        return tables


def tables_to_text(tables: list[list[list[str]]]) -> str:
    parts: list[str] = []
    for table in tables:
        for row in table:
            if row and any(cell for cell in row):
                parts.append(" | ".join(str(cell or "").strip() for cell in row))
        parts.append("")
    return "\n".join(parts)


def extract_with_ocr(data: bytes, *, ocr_timeout_sec: int) -> str:
    with start_span("pdf.ocr", tracer_name=__name__) as span:
        if not OCR_AVAILABLE or PILImage is None or pytesseract is None:
            set_span_attributes(span, {"pdf.ocr_available": False})
            return ""

        set_span_attributes(span, {"pdf.ocr_available": True})
        try:
            doc = _open_pdf_document(data)
            parts: list[str] = []
            try:
                set_span_attributes(span, {"pdf.page_count": len(doc)})
                for page_index in range(len(doc)):
                    page = doc.load_page(page_index)
                    pix = page.get_pixmap(dpi=150, alpha=False)
                    img = PILImage.frombytes(
                        "RGB",
                        (pix.width, pix.height),
                        pix.samples,
                    )
                    text = pytesseract.image_to_string(
                        img,
                        timeout=ocr_timeout_sec,
                    )
                    if text.strip():
                        parts.append(text)
            finally:
                doc.close()
            combined = "\n\n".join(parts)
            set_span_attributes(span, {"pdf.ocr_chars": len(combined)})
            return combined
        except RuntimeError as exc:
            message = str(exc).lower()
            if "time" in message and "out" in message:
                raise OCRTimeoutError(
                    f"OCR exceeded timeout of {ocr_timeout_sec} seconds."
                ) from exc
            return ""
        except Exception:
            return ""


def extract_pdf_bytes(
    data: bytes,
    allow_ocr: bool = True,
    *,
    max_pdf_pages: int = 500,
    ocr_timeout_sec: int = 30,
) -> ExtractionResult:
    with start_span(
        "pdf.extract",
        tracer_name=__name__,
        attributes={
            "pdf.allow_ocr": allow_ocr,
            "pdf.max_pages": max_pdf_pages,
            "pdf.ocr_timeout_sec": ocr_timeout_sec,
        },
    ) as span:
        page_count = validate_pdf_page_cap(data, max_pdf_pages)
        text = extract_text_pymupdf(data)
        tables = extract_tables_pdfplumber(data)
        table_text = tables_to_text(tables)
        combined = f"{text}\n\n{table_text}".strip()
        ocr_used = False

        if len(combined) < 100 and allow_ocr and OCR_AVAILABLE:
            ocr_text = extract_with_ocr(data, ocr_timeout_sec=ocr_timeout_sec)
            if ocr_text:
                combined = f"{combined}\n\n{ocr_text}".strip()
                ocr_used = True

        set_span_attributes(
            span,
            {
                "pdf.page_count": page_count,
                "pdf.text_chars": len(combined or ""),
                "pdf.table_count": len(tables),
                "pdf.ocr_used": ocr_used,
            },
        )
        return ExtractionResult(text=combined or "", tables=tables, ocr_used=ocr_used)


def extract_pdf(
    pdf_url: str,
    allow_ocr: bool = True,
    timeout_sec: int = 60,
) -> tuple[str, list[list[list[str]]], bool]:
    import requests  # type: ignore[import-untyped]

    response = requests.get(pdf_url, timeout=timeout_sec)
    response.raise_for_status()
    result = extract_pdf_bytes(response.content, allow_ocr=allow_ocr)
    return result.text, result.tables, result.ocr_used
