"""Tests for scripts/generate_pdfs.py, the synthetic trucking-paperwork generator.

They prove the generator is deterministic, produces exactly the requested page
count, renders every kind, keeps the committed fixture reproducible, and never
writes personal or real-looking identifiers.
"""

from __future__ import annotations

import importlib.util
import io
import re
import sys
from pathlib import Path
from types import ModuleType

import pdfplumber
import pymupdf
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_pdfs.py"


def load_generator() -> ModuleType:
    module_spec = importlib.util.spec_from_file_location("generate_pdfs", GENERATOR_PATH)
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules["generate_pdfs"] = module
    module_spec.loader.exec_module(module)
    return module


generate_pdfs = load_generator()


def page_texts(pdf_bytes: bytes) -> list[str]:
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        return [page.get_text() for page in document]


def page_count_of(pdf_bytes: bytes) -> int:
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        return len(document)


def test_same_seed_gives_same_text_and_different_seed_differs() -> None:
    first_text = page_texts(generate_pdfs.generate_pdf_bytes("mixed", 12, seed=42))
    second_text = page_texts(generate_pdfs.generate_pdf_bytes("mixed", 12, seed=42))
    other_seed_text = page_texts(generate_pdfs.generate_pdf_bytes("mixed", 12, seed=43))

    assert first_text == second_text
    assert first_text != other_seed_text


def test_same_seed_gives_same_bytes() -> None:
    # reportlab runs with invariant=1 (fixed timestamps and document ID), so the
    # bytes are stable too, not just the text.
    assert generate_pdfs.generate_pdf_bytes("invoice", 6, seed=3) == generate_pdfs.generate_pdf_bytes(
        "invoice", 6, seed=3
    )


@pytest.mark.parametrize(
    ("kind", "page_count"),
    [
        ("rate-confirmation", 1),
        ("bill-of-lading", 2),
        ("invoice", 3),
        ("settlement", 9),
        ("mixed", 40),
        ("invoice", 120),
    ],
)
def test_page_count_is_exact(kind: str, page_count: int) -> None:
    pdf_bytes = generate_pdfs.generate_pdf_bytes(kind, page_count, seed=1)

    assert page_count_of(pdf_bytes) == page_count
    if kind != "mixed":
        texts = page_texts(pdf_bytes)
        assert f"Page 1 of {page_count}" in texts[0]
        assert f"Page {page_count} of {page_count}" in texts[-1]


@pytest.mark.parametrize("kind", generate_pdfs.KIND_CHOICES)
def test_every_kind_renders_text_and_ruled_tables(kind: str) -> None:
    pdf_bytes = generate_pdfs.generate_pdf_bytes(kind, 4, seed=5)

    full_text = "\n".join(page_texts(pdf_bytes))
    assert "Sample Carrier LLC" in full_text
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as document:
        table_count = sum(len(page.extract_tables()) for page in document.pages)
    assert table_count >= 1


def test_long_tables_repeat_their_header_on_every_page() -> None:
    pdf_bytes = generate_pdfs.generate_pdf_bytes("invoice", 8, seed=2)
    texts = page_texts(pdf_bytes)

    # Page 1 starts the line items; the body pages continue them with the header repeated.
    continued_pages = [text for text in texts if "Line items (continued)" in text]
    assert len(continued_pages) >= 3
    for text in continued_pages:
        assert "Amount (USD)" in text
    assert any("Accessorial charges" in text for text in texts)
    assert any("Payment terms" in text for text in texts)


def test_mixed_packet_interleaves_document_kinds() -> None:
    full_text = "\n".join(page_texts(generate_pdfs.generate_pdf_bytes("mixed", 60, seed=1)))

    for title in ("Rate Confirmation", "Straight Bill of Lading", "Freight Invoice", "Driver Settlement Statement"):
        assert title in full_text
    assert "Packet page 60 of 60" in full_text


def test_fixture_mode_reproduces_committed_fixture() -> None:
    committed_fixture = (REPO_ROOT / "fixtures" / "sample.pdf").read_bytes()
    regenerated = generate_pdfs.generate_pdf_bytes("load-confirmation", generate_pdfs.FIXTURE_PAGE_COUNT, seed=0)

    assert regenerated == committed_fixture


@pytest.mark.parametrize("kind", ["mixed", "load-confirmation"])
def test_metadata_and_bytes_carry_no_personal_info(kind: str) -> None:
    pdf_bytes = generate_pdfs.generate_pdf_bytes(kind, 5, seed=9)
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        metadata = document.metadata

    assert metadata["author"] == "pdf-bench"
    if kind != "load-confirmation":  # the fixture keeps its original metadata byte for byte
        assert metadata["creator"] == "pdf-bench"
    lowered_bytes = pdf_bytes.lower()
    for forbidden in (b"/home/", b"/users/", b"/tmp/", b"trey", b"murray", b"@gmail"):
        assert forbidden not in lowered_bytes


def test_identifiers_are_obviously_fake() -> None:
    full_text = "\n".join(page_texts(generate_pdfs.generate_pdf_bytes("mixed", 30, seed=11)))

    assert set(re.findall(r"MC-\d+", full_text)) == {"MC-000000"}
    assert set(re.findall(r"USDOT \d+", full_text)) == {"USDOT 0000000"}
    assert set(re.findall(r", ([A-Z]{2}) \d{5}", full_text)) <= {"ZZ"}
    assert set(re.findall(r", [A-Z]{2} (\d{5})", full_text)) <= {"00000"}
    assert set(re.findall(r"\(\d{3}\) \d{3}-\d{4}", full_text)) <= {"(000) 000-0000"}


def test_scanned_output_has_no_text_layer() -> None:
    pdf_bytes = generate_pdfs.generate_pdf_bytes("bill-of-lading", 2, seed=1, scanned=True, scan_dpi=72)

    assert page_count_of(pdf_bytes) == 2
    assert "".join(page_texts(pdf_bytes)).strip() == ""


def test_cli_batch_writes_count_files(tmp_path: Path) -> None:
    generate_pdfs.main(["--kind", "settlement", "--pages", "2", "--count", "3", "--seed", "4", "--out-dir", str(tmp_path)])

    written = sorted(tmp_path.glob("*.pdf"))
    assert [path.name for path in written] == [
        "settlement-0002p-seed4-001.pdf",
        "settlement-0002p-seed4-002.pdf",
        "settlement-0002p-seed4-003.pdf",
    ]
    # Each document in a batch gets its own seed.
    assert written[0].read_bytes() != written[1].read_bytes()


@pytest.mark.parametrize(
    "bad_arguments",
    [
        ["--pages", "0", "--out", "x.pdf"],
        ["--pages", "2", "--count", "2", "--out", "x.pdf"],
        ["--pages", "2"],
    ],
)
def test_cli_rejects_bad_arguments(bad_arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        generate_pdfs.main(bad_arguments)
