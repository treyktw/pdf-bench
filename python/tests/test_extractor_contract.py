import pytest

from pdf_bench.extractor import (
    ExtractionPermanentError,
    extract_pdf_bytes,
)


def test_invalid_pdf_is_permanent_error() -> None:
    with pytest.raises(ExtractionPermanentError):
        extract_pdf_bytes(b"not a pdf", allow_ocr=False)


def test_synthetic_fixture_yields_text_and_tables() -> None:
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "sample.pdf"
    result = extract_pdf_bytes(fixture.read_bytes(), allow_ocr=False)

    assert "Load Confirmation EXAMPLE-LOAD-1001" in result.text
    # Two tables (stops + rates) per page across the 10-page fixture.
    assert len(result.tables) == 20
    assert result.tables[0][0] == ["Stop", "Type", "City", "Window", "Pieces", "Weight (lb)"]
    assert result.ocr_used is False


def test_page_cap_is_permanent_error() -> None:
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[2] / "fixtures" / "sample.pdf"
    with pytest.raises(ExtractionPermanentError):
        extract_pdf_bytes(fixture.read_bytes(), allow_ocr=False, max_pdf_pages=3)
