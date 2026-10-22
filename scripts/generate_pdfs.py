"""Generate synthetic trucking paperwork PDFs for the extraction benchmark.

Every company, person, address, and number in the output is invented and
obviously fake (state "ZZ", ZIP 00000, MC-000000, USDOT 0000000, example.com
addresses). The documents are shaped like the paperwork a freight back office
actually extracts from, so both the text path and the pdfplumber table path
have real work to do:

- rate-confirmation   broker -> carrier load tender with stops and rates
- bill-of-lading      shipper/consignee/commodity paperwork
- invoice             carrier freight bill with line items and accessorials
- settlement          driver pay statement (trips, deductions, net pay)
- mixed               a load packet that interleaves the four kinds above
- load-confirmation   the original benchmark fixture layout (fixtures/sample.pdf)

Long documents are really long: line-item tables continue across pages with
the header row repeated, followed by accessorial tables, stop lists, and
terms-and-conditions prose, all under a running header and a "Page x of N"
footer. Each page is laid out explicitly, so `--pages N` produces exactly N
pages.

Output is deterministic: the same `--kind`, `--pages`, and `--seed` give the
same text and, outside `--scanned`, the same bytes (reportlab `invariant=1`).

Usage (from the repo root, with the Python dev extras installed):

    python scripts/generate_pdfs.py --kind invoice --pages 25 --out /tmp/invoice.pdf
    python scripts/generate_pdfs.py --kind mixed --pages 1000 --seed 7 --out /tmp/packet.pdf
    python scripts/generate_pdfs.py --kind settlement --pages 3 --count 20 --out-dir /tmp/batch
    python scripts/generate_pdfs.py --kind bill-of-lading --pages 5 --scanned --out /tmp/scan.pdf
    python scripts/generate_pdfs.py --fixture        # rewrites fixtures/sample.pdf
"""

from __future__ import annotations

import argparse
import io
import random
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import TypedDict

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, StyleSheet1, getSampleStyleSheet
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    KeepInFrame,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "fixtures" / "sample.pdf"
FIXTURE_PAGE_COUNT = 10

METADATA_AUTHOR = "pdf-bench"
METADATA_CREATOR = "pdf-bench"
METADATA_SUBJECT = "Synthetic trucking paperwork for benchmarking (not a real document)"

MIN_PAGES = 1
MAX_PAGES = 5000
DEFAULT_SCAN_DPI = 150

STANDARD_KINDS = ("rate-confirmation", "bill-of-lading", "invoice", "settlement")
KIND_CHOICES = (*STANDARD_KINDS, "mixed", "load-confirmation")

# Page geometry for the running-header layout. The frame is what each page's
# content must fit inside; KeepInFrame shrinks anything that would not.
PAGE_WIDTH, PAGE_HEIGHT = LETTER
SIDE_MARGIN = 54
TOP_MARGIN = 66
BOTTOM_MARGIN = 58
FRAME_WIDTH = PAGE_WIDTH - 2 * SIDE_MARGIN
FRAME_HEIGHT = PAGE_HEIGHT - TOP_MARGIN - BOTTOM_MARGIN - 12  # minus frame padding

FULL_PAGE_TABLE_ROWS = 40
FIRST_PAGE_TABLE_ROWS = 14
CLAUSES_PER_PROSE_PAGE = 14
SYNTHETIC_BANNER = "SYNTHETIC SAMPLE - NOT A REAL DOCUMENT"
BASE_DATE = date(2026, 1, 5)

# ---------------------------------------------------------------------------
# Synthetic vocabulary. Nothing here is a real company, person, or number.
# ---------------------------------------------------------------------------

SYNTHETIC_CITIES = [
    "Springfield, ZZ",
    "Riverton, ZZ",
    "Lakeside, ZZ",
    "Fairview, ZZ",
    "Oakdale, ZZ",
    "Hillcrest, ZZ",
    "Maple Grove, ZZ",
    "Pine Bluff, ZZ",
]
STREET_NAMES = ["Example Ave", "Sample St", "Placeholder Rd", "Demo Blvd", "Test Pkwy", "Mock Ln"]
SHIPPER_NAMES = [
    "Example Shipper Co.",
    "Placeholder Foods Inc.",
    "Sample Paper Mills LLC",
    "Demo Appliance Works",
    "Fictional Beverage Co.",
]
CONSIGNEE_NAMES = [
    "Mock Distribution Center",
    "Example Retail DC #00",
    "Sample Grocers Warehouse",
    "Demo Hardware Depot",
    "Placeholder Cold Storage",
]
BROKER_NAMES = ["Demo Logistics Inc.", "Test Freight Brokerage LLC", "Example Freight Partners"]
CARRIER_NAME = "Sample Carrier LLC"
CARRIER_MC = "MC-000000"
CARRIER_DOT = "USDOT 0000000"
FAKE_PHONE = "(000) 000-0000"
PEOPLE_NAMES = [
    "Jordan Q. Testdriver",
    "Alex B. Sampleton",
    "Casey R. Placeholder",
    "Morgan T. Example",
    "Riley D. Fakename",
]
DRIVER_NAME = "Jordan Q. Testdriver"
EQUIPMENT_TYPES = ["53' Dry Van", "53' Reefer", "48' Flatbed", "53' Dry Van (team)"]
COMMODITIES = [
    "Canned goods (sample)",
    "Paper products (sample)",
    "Small appliances (sample)",
    "Bottled water (sample)",
    "Hardware, boxed (sample)",
    "Frozen desserts (sample)",
]
PACKAGE_TYPES = ["PLT", "CTN", "DRUM", "CRATE", "BNDL"]
ACCESSORIAL_TYPES = [
    ("DET", "Detention after 2 free hours", "hour", 50.0),
    ("LAY", "Layover", "day", 250.0),
    ("TONU", "Truck ordered not used", "each", 150.0),
    ("LUMP", "Lumper (receipt required)", "each", 175.0),
    ("STOP", "Additional stop", "each", 75.0),
    ("TARP", "Tarping", "each", 100.0),
    ("DRVA", "Driver assist unload", "each", 125.0),
    ("REEF", "Reefer pre-cool", "each", 60.0),
]
DEDUCTION_TYPES = [
    ("FUEL", "Fuel card advance"),
    ("INS", "Occupational accident (sample)"),
    ("ESC", "Escrow contribution"),
    ("TOLL", "Tolls"),
    ("ELD", "ELD subscription (sample)"),
    ("ADV", "Cash advance"),
]
REIMBURSEMENT_TYPES = ["Scale ticket", "Lumper paid by driver", "Parking", "Washout", "Tolls (cash)"]

CLAUSE_TOPICS = [
    "Detention",
    "Layover",
    "Truck Ordered Not Used",
    "Lumper Fees",
    "Cargo Claims",
    "Payment Terms",
    "No Re-brokering",
    "Insurance",
    "Temperature Control",
    "Tracking",
    "Accessorial Approval",
    "Indemnity",
    "Governing Agreement",
    "Confidentiality",
    "Fuel Surcharge",
    "Cargo Security",
    "Seals",
    "Appointments",
]
CLAUSE_SENTENCES = [
    "This clause is synthetic text generated for benchmarking and has no legal effect.",
    "Carrier shall notify the example dispatcher at least {hours} hours before any expected delay.",
    "Charges under this clause accrue at the example rate of ${rate}.00 per {unit}.",
    "Claims must be submitted in writing within {days} days of the example delivery date.",
    "Receipts are required for any reimbursement above ${rate}.00.",
    "None of the parties, amounts, or reference numbers in this document correspond to a real shipment, company, or person.",
    "Payment will be issued {days} days after receipt of a signed delivery receipt and invoice.",
    "Carrier agrees that freight will remain on the tendered equipment for the duration of the example transit.",
    "Any change to the stop schedule must be confirmed in writing by both example parties.",
    "Reefer units must be set to {temp} F continuous and a download provided on request.",
    "Tracking updates are expected every {hours} hours while the load is in transit.",
    "Failure to comply may result in a deduction of ${rate}.00 from the example settlement.",
]

# ---------------------------------------------------------------------------
# Typed records
# ---------------------------------------------------------------------------


class PageLabel(TypedDict):
    """What the running header and footer print on one physical page."""

    document_title: str
    document_number: str
    page_in_document: int
    document_page_count: int


class PlannedPage(TypedDict):
    """One physical page: the flowables that fill it and its header/footer label."""

    flowables: list[Flowable]
    label: PageLabel


class GenerationRequest(TypedDict):
    """Everything needed to produce one PDF file."""

    kind: str
    page_count: int
    seed: int
    scanned: bool
    scan_dpi: int
    output_path: Path


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def build_styles() -> StyleSheet1:
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("DocTitle", parent=styles["Title"], fontSize=16, spaceAfter=4))
    styles.add(ParagraphStyle("Section", parent=styles["Heading2"], fontSize=11, spaceBefore=2, spaceAfter=4))
    styles.add(ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, leading=10))
    styles.add(ParagraphStyle("Clause", parent=styles["Normal"], fontSize=8.5, leading=10.5, spaceAfter=5))
    return styles


def money(amount: float) -> str:
    return f"{amount:,.2f}"


def synthetic_address(random_source: random.Random) -> str:
    street_number = random_source.randint(100, 9899)
    street_name = random_source.choice(STREET_NAMES)
    city = random_source.choice(SYNTHETIC_CITIES)
    return f"{street_number} {street_name}, {city} 00000"


def synthetic_reference(prefix: str, random_source: random.Random) -> str:
    return f"{prefix}-SAMPLE-{random_source.randint(0, 999_999):06d}"


def offset_date(day_offset: int) -> str:
    return (BASE_DATE + timedelta(days=day_offset)).isoformat()


def allocate_pages(total_pages: int, section_weights: list[float]) -> list[int]:
    """Split `total_pages` across sections by weight (largest remainder, deterministic)."""

    if total_pages <= 0:
        return [0 for _ in section_weights]
    weight_sum = sum(section_weights)
    exact_shares = [total_pages * weight / weight_sum for weight in section_weights]
    allocated = [int(share) for share in exact_shares]
    remaining = total_pages - sum(allocated)
    by_remainder = sorted(
        range(len(section_weights)),
        key=lambda index: (exact_shares[index] - allocated[index], -index),
        reverse=True,
    )
    for index in by_remainder[:remaining]:
        allocated[index] += 1
    return allocated


def ruled_table(rows: list[list[str]], column_fractions: list[float], font_size: float = 7.5) -> Table:
    """A gridded table (pdfplumber finds these) with a shaded, bold header row."""

    column_widths = [FRAME_WIDTH * fraction for fraction in column_fractions]
    table = Table(rows, colWidths=column_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), font_size),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


def key_value_block(pairs: list[tuple[str, str]], columns: int = 2) -> Table:
    """An unruled label/value block, like the header area of real paperwork."""

    cells: list[list[str]] = []
    row: list[str] = []
    for label, value in pairs:
        row.extend([label, value])
        if len(row) == columns * 2:
            cells.append(row)
            row = []
    if row:
        row.extend([""] * (columns * 2 - len(row)))
        cells.append(row)
    label_width = FRAME_WIDTH * 0.14
    value_width = FRAME_WIDTH / columns - label_width
    table = Table(cells, colWidths=[label_width, value_width] * columns)
    style_commands = [
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]
    for column_index in range(0, columns * 2, 2):
        style_commands.append(("FONTNAME", (column_index, 0), (column_index, -1), "Helvetica-Bold"))
    table.setStyle(TableStyle(style_commands))
    return table


def signature_block(signers: list[str]) -> Table:
    rows = [[f"{signer}:", "_" * 34, "Date:", "_" * 14] for signer in signers]
    table = Table(rows, colWidths=[FRAME_WIDTH * 0.26, FRAME_WIDTH * 0.44, FRAME_WIDTH * 0.1, FRAME_WIDTH * 0.2])
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return table


def paginate_table(
    heading: str,
    header_row: list[str],
    body_rows: list[list[str]],
    column_fractions: list[float],
    page_count: int,
    styles: StyleSheet1,
    continues_first_page: bool = False,
) -> list[list[Flowable]]:
    """Lay a long table over exactly `page_count` pages, repeating the header row.

    `continues_first_page` marks a table whose opening rows already sit on the
    document's first page, so every page here is captioned "(continued)".

    Rows are chunked explicitly (rather than letting reportlab split a single
    Table) so the page count is exact; the result looks the same as a flowing
    table with `repeatRows=1`: header on every page, "(continued)" captions.
    """

    pages: list[list[Flowable]] = []
    for page_index in range(page_count):
        chunk = body_rows[page_index * FULL_PAGE_TABLE_ROWS : (page_index + 1) * FULL_PAGE_TABLE_ROWS]
        is_continuation = continues_first_page or page_index > 0
        caption = f"{heading} (continued)" if is_continuation else heading
        if page_count > 1:
            caption += f" - part {page_index + 1} of {page_count}"
        pages.append(
            [
                Paragraph(caption, styles["Section"]),
                ruled_table([header_row, *chunk], column_fractions),
            ]
        )
    return pages


def rows_for_section(section_page_count: int, random_source: random.Random) -> int:
    """Full pages of rows, with a partially filled last page so it reads naturally."""

    if section_page_count <= 0:
        return 0
    last_page_rows = random_source.randint(FULL_PAGE_TABLE_ROWS // 2, FULL_PAGE_TABLE_ROWS)
    return (section_page_count - 1) * FULL_PAGE_TABLE_ROWS + last_page_rows


def prose_pages(
    heading: str,
    page_count: int,
    random_source: random.Random,
    styles: StyleSheet1,
) -> list[list[Flowable]]:
    """Numbered terms-and-conditions clauses, CLAUSES_PER_PROSE_PAGE per page."""

    pages: list[list[Flowable]] = []
    clause_number = 1
    for page_index in range(page_count):
        flowables: list[Flowable] = [
            Paragraph(f"{heading} - page {page_index + 1} of {page_count}", styles["Section"])
        ]
        for _ in range(CLAUSES_PER_PROSE_PAGE):
            topic = CLAUSE_TOPICS[(clause_number - 1) % len(CLAUSE_TOPICS)]
            sentences = [
                template.format(
                    hours=random_source.choice([1, 2, 4, 6]),
                    rate=random_source.choice([25, 50, 75, 100, 150]),
                    unit=random_source.choice(["hour", "day", "occurrence"]),
                    days=random_source.choice([7, 15, 30, 45, 60]),
                    temp=random_source.choice([34, 36, 0, -10]),
                )
                for template in random_source.sample(CLAUSE_SENTENCES, 4)
            ]
            flowables.append(
                Paragraph(f"<b>{clause_number}. {topic}.</b> " + " ".join(sentences), styles["Clause"])
            )
            clause_number += 1
        pages.append(flowables)
    return pages


# ---------------------------------------------------------------------------
# Row factories (all values synthetic)
# ---------------------------------------------------------------------------


def stop_rows(row_count: int, random_source: random.Random) -> list[list[str]]:
    rows: list[list[str]] = []
    for stop_index in range(row_count):
        stop_type = "Pickup" if stop_index == 0 or random_source.random() < 0.15 else "Delivery"
        facility = random_source.choice(SHIPPER_NAMES if stop_type == "Pickup" else CONSIGNEE_NAMES)
        start_hour = random_source.randint(6, 16)
        rows.append(
            [
                str(stop_index + 1),
                stop_type,
                facility,
                random_source.choice(SYNTHETIC_CITIES),
                f"{offset_date(stop_index // 3)} {start_hour:02d}:00-{start_hour + 2:02d}:00",
                synthetic_reference("PO", random_source),
                str(random_source.randint(1, 30)),
                f"{random_source.randint(500, 9_500):,}",
            ]
        )
    return rows


def accessorial_schedule_rows(row_count: int, random_source: random.Random) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_index in range(row_count):
        code, description, unit, base_rate = ACCESSORIAL_TYPES[row_index % len(ACCESSORIAL_TYPES)]
        tier = row_index // len(ACCESSORIAL_TYPES) + 1
        rate = base_rate + random_source.choice([0, 5, 10, 25])
        rows.append(
            [
                f"{code}-{tier:03d}",
                f"{description}, tier {tier}",
                unit,
                money(rate),
                money(rate * random_source.choice([4, 6, 8, 10])),
            ]
        )
    return rows


def commodity_rows(row_count: int, random_source: random.Random) -> list[list[str]]:
    rows: list[list[str]] = []
    for line_index in range(row_count):
        handling_units = random_source.randint(1, 26)
        rows.append(
            [
                str(line_index + 1),
                str(handling_units),
                random_source.choice(["PLT", "SKID"]),
                str(handling_units * random_source.randint(4, 60)),
                random_source.choice(PACKAGE_TYPES),
                f"{random_source.randint(200, 44_000):,}",
                str(random_source.choice([50, 55, 60, 65, 70, 77.5, 85, 92.5, 100, 125])),
                "00000-0",
                random_source.choice(COMMODITIES),
            ]
        )
    return rows


def invoice_line_rows(row_count: int, random_source: random.Random) -> tuple[list[list[str]], float]:
    rows: list[list[str]] = []
    total = 0.0
    for line_index in range(row_count):
        quantity = random_source.choice([1, 1, 1, 2, 3])
        unit_price = float(random_source.randint(450, 4_200))
        amount = quantity * unit_price
        total += amount
        origin, destination = random_source.sample(SYNTHETIC_CITIES, 2)
        rows.append(
            [
                str(line_index + 1),
                offset_date(line_index // 4),
                f"EXAMPLE-LOAD-{2000 + line_index}",
                f"Linehaul {origin.split(',')[0]} to {destination.split(',')[0]}",
                str(quantity),
                money(unit_price),
                money(amount),
            ]
        )
    return rows, total


def accessorial_charge_rows(row_count: int, random_source: random.Random) -> tuple[list[list[str]], float]:
    rows: list[list[str]] = []
    total = 0.0
    for charge_index in range(row_count):
        code, description, unit, base_rate = random_source.choice(ACCESSORIAL_TYPES)
        quantity = random_source.randint(1, 4) if unit == "hour" else 1
        amount = base_rate * quantity
        total += amount
        rows.append(
            [
                offset_date(charge_index // 3),
                f"EXAMPLE-LOAD-{2000 + random_source.randint(0, 999)}",
                code,
                f"{description} x{quantity}",
                money(amount),
            ]
        )
    return rows, total


def trip_rows(row_count: int, random_source: random.Random) -> tuple[list[list[str]], float]:
    rows: list[list[str]] = []
    total = 0.0
    for trip_index in range(row_count):
        loaded_miles = random_source.randint(120, 1_400)
        empty_miles = random_source.randint(0, 180)
        rate_per_mile = random_source.choice([0.55, 0.58, 0.60, 0.62, 0.65])
        pay = round(loaded_miles * rate_per_mile, 2)
        total += pay
        origin, destination = random_source.sample(SYNTHETIC_CITIES, 2)
        rows.append(
            [
                f"TRIP-{10_000 + trip_index}",
                offset_date(trip_index // 2),
                origin,
                destination,
                f"{loaded_miles:,}",
                str(empty_miles),
                f"{rate_per_mile:.2f}",
                money(pay),
            ]
        )
    return rows, total


def deduction_rows(row_count: int, random_source: random.Random) -> tuple[list[list[str]], float]:
    rows: list[list[str]] = []
    total = 0.0
    for deduction_index in range(row_count):
        code, description = random_source.choice(DEDUCTION_TYPES)
        amount = float(random_source.randint(15, 420))
        total += amount
        rows.append([offset_date(deduction_index // 3), code, description, money(amount)])
    return rows, total


def reimbursement_rows(row_count: int, random_source: random.Random) -> tuple[list[list[str]], float]:
    rows: list[list[str]] = []
    total = 0.0
    for reimbursement_index in range(row_count):
        amount = float(random_source.randint(10, 260))
        total += amount
        rows.append(
            [
                offset_date(reimbursement_index // 2),
                random_source.choice(REIMBURSEMENT_TYPES),
                synthetic_reference("RCPT", random_source),
                money(amount),
            ]
        )
    return rows, total


# ---------------------------------------------------------------------------
# Document builders. Each returns (title, number, pages) with len(pages) == page_count.
# Page 1 is a summary page; pages 2..N-1 are body sections split by weight;
# page N (when N >= 2) is a closing page with totals and signatures.
# ---------------------------------------------------------------------------

DocumentPages = tuple[str, str, list[list[Flowable]]]

STOP_HEADER = ["Stop", "Type", "Facility", "City", "Appointment", "Ref #", "Pcs", "Weight (lb)"]
STOP_COLUMNS = [0.05, 0.08, 0.23, 0.14, 0.2, 0.15, 0.05, 0.1]
ACCESSORIAL_HEADER = ["Code", "Description", "Unit", "Rate (USD)", "Max (USD)"]
ACCESSORIAL_COLUMNS = [0.12, 0.48, 0.1, 0.15, 0.15]


def build_rate_confirmation(page_count: int, random_source: random.Random, styles: StyleSheet1) -> DocumentPages:
    document_number = synthetic_reference("RC", random_source)
    title = "Rate Confirmation"
    body_page_count = max(page_count - 2, 0)
    stop_pages, accessorial_pages, terms_pages = allocate_pages(body_page_count, [0.5, 0.2, 0.3])

    first_page_stops = 4 if page_count == 1 else FIRST_PAGE_TABLE_ROWS
    all_stops = stop_rows(first_page_stops + rows_for_section(stop_pages, random_source), random_source)
    linehaul = float(random_source.randint(900, 6_500))
    fuel_surcharge = round(linehaul * random_source.choice([0.12, 0.18, 0.22]), 2)
    extra_stop_charge = 75.0 * max(len(all_stops) - 2, 0)
    total = linehaul + fuel_surcharge + extra_stop_charge

    rate_table = ruled_table(
        [
            ["Charge", "Amount (USD)"],
            ["Linehaul (example)", money(linehaul)],
            ["Fuel surcharge (example)", money(fuel_surcharge)],
            [f"Additional stops x{max(len(all_stops) - 2, 0)}", money(extra_stop_charge)],
            ["Total carrier pay", money(total)],
        ],
        [0.7, 0.3],
        font_size=8,
    )
    first_page: list[Flowable] = [
        Paragraph(f"{title} {document_number}", styles["DocTitle"]),
        key_value_block(
            [
                ("Broker", random_source.choice(BROKER_NAMES)),
                ("Carrier", f"{CARRIER_NAME} ({CARRIER_MC}, {CARRIER_DOT})"),
                ("Dispatcher", random_source.choice(PEOPLE_NAMES)),
                ("Driver", DRIVER_NAME),
                ("Phone", FAKE_PHONE),
                ("Email", "dispatch@example.com"),
                ("Equipment", random_source.choice(EQUIPMENT_TYPES)),
                ("Commodity", random_source.choice(COMMODITIES)),
                ("Load #", f"EXAMPLE-LOAD-{random_source.randint(1000, 9999)}"),
                ("Tendered", offset_date(-1)),
            ]
        ),
        Spacer(1, 8),
        Paragraph("Stops", styles["Section"]),
        ruled_table([STOP_HEADER, *all_stops[:first_page_stops]], STOP_COLUMNS),
        Spacer(1, 8),
        Paragraph("Rate", styles["Section"]),
        rate_table,
    ]
    if page_count == 1:
        first_page += [Spacer(1, 10), signature_block(["Carrier signature"])]
        return title, document_number, [first_page]

    pages: list[list[Flowable]] = [first_page]
    pages += paginate_table("Stop schedule", STOP_HEADER, all_stops[first_page_stops:], STOP_COLUMNS, stop_pages, styles, continues_first_page=True)
    accessorials = accessorial_schedule_rows(rows_for_section(accessorial_pages, random_source), random_source)
    pages += paginate_table(
        "Accessorial schedule", ACCESSORIAL_HEADER, accessorials, ACCESSORIAL_COLUMNS, accessorial_pages, styles
    )
    pages += prose_pages("Terms and conditions", terms_pages, random_source, styles)
    pages.append(
        [
            Paragraph("Confirmation and acceptance", styles["Section"]),
            Paragraph(
                f"Total carrier pay for {document_number}: USD {money(total)} across {len(all_stops)} stops. "
                "By signing, the example carrier accepts the synthetic terms above.",
                styles["Normal"],
            ),
            Spacer(1, 8),
            rate_table,
            Spacer(1, 12),
            signature_block(["Broker representative", "Carrier representative"]),
        ]
    )
    return title, document_number, pages


COMMODITY_HEADER = ["Line", "HU", "HU type", "Pkgs", "Pkg type", "Weight (lb)", "Class", "NMFC", "Description"]
COMMODITY_COLUMNS = [0.06, 0.06, 0.09, 0.07, 0.09, 0.12, 0.08, 0.1, 0.33]


def build_bill_of_lading(page_count: int, random_source: random.Random, styles: StyleSheet1) -> DocumentPages:
    document_number = synthetic_reference("BOL", random_source)
    title = "Straight Bill of Lading"
    body_page_count = max(page_count - 2, 0)
    commodity_pages, terms_pages = allocate_pages(body_page_count, [0.65, 0.35])

    first_page_lines = 5 if page_count == 1 else FIRST_PAGE_TABLE_ROWS
    all_lines = commodity_rows(first_page_lines + rows_for_section(commodity_pages, random_source), random_source)
    total_weight = sum(int(line[5].replace(",", "")) for line in all_lines)
    total_units = sum(int(line[1]) for line in all_lines)

    first_page: list[Flowable] = [
        Paragraph(f"{title} {document_number}", styles["DocTitle"]),
        key_value_block(
            [
                ("Shipper", f"{random_source.choice(SHIPPER_NAMES)}\n{synthetic_address(random_source)}"),
                ("Consignee", f"{random_source.choice(CONSIGNEE_NAMES)}\n{synthetic_address(random_source)}"),
                ("Bill to", f"{random_source.choice(BROKER_NAMES)}\n{synthetic_address(random_source)}"),
                ("Carrier", f"{CARRIER_NAME}\n{CARRIER_MC} / {CARRIER_DOT}"),
                ("PRO #", synthetic_reference("PRO", random_source)),
                ("Seal #", synthetic_reference("SEAL", random_source)),
                ("Trailer", f"TRL-{random_source.randint(100, 999)}"),
                ("Ship date", offset_date(0)),
                ("Terms", random_source.choice(["Prepaid", "Collect", "Third party"])),
                ("Contact", f"{random_source.choice(PEOPLE_NAMES)}, {FAKE_PHONE}"),
            ]
        ),
        Spacer(1, 8),
        Paragraph("Commodity description", styles["Section"]),
        ruled_table([COMMODITY_HEADER, *all_lines[:first_page_lines]], COMMODITY_COLUMNS),
    ]
    totals_table = ruled_table(
        [
            ["Total lines", "Total handling units", "Total weight (lb)"],
            [str(len(all_lines)), str(total_units), f"{total_weight:,}"],
        ],
        [0.33, 0.34, 0.33],
        font_size=8,
    )
    certification = Paragraph(
        "This is to certify that the above-named example materials are properly classified, packaged, "
        "marked, and labeled. Synthetic document: no real shipment, shipper, or carrier is described.",
        styles["Small"],
    )
    signers = ["Shipper", "Carrier (driver)", "Consignee"]
    if page_count == 1:
        first_page += [Spacer(1, 8), totals_table, Spacer(1, 6), certification, signature_block(signers)]
        return title, document_number, [first_page]

    pages: list[list[Flowable]] = [first_page]
    pages += paginate_table(
        "Commodity description", COMMODITY_HEADER, all_lines[first_page_lines:], COMMODITY_COLUMNS, commodity_pages, styles,
        continues_first_page=True,
    )
    pages += prose_pages("Contract terms", terms_pages, random_source, styles)
    pages.append(
        [
            Paragraph("Totals and receipt", styles["Section"]),
            totals_table,
            Spacer(1, 10),
            certification,
            Spacer(1, 6),
            signature_block(signers),
        ]
    )
    return title, document_number, pages


INVOICE_HEADER = ["Line", "Date", "Load #", "Description", "Qty", "Unit (USD)", "Amount (USD)"]
INVOICE_COLUMNS = [0.06, 0.12, 0.17, 0.33, 0.06, 0.12, 0.14]
CHARGE_HEADER = ["Date", "Load #", "Code", "Description", "Amount (USD)"]
CHARGE_COLUMNS = [0.13, 0.19, 0.09, 0.45, 0.14]


def build_invoice(page_count: int, random_source: random.Random, styles: StyleSheet1) -> DocumentPages:
    document_number = synthetic_reference("INV", random_source)
    title = "Freight Invoice"
    body_page_count = max(page_count - 2, 0)
    line_pages, charge_pages, terms_pages = allocate_pages(body_page_count, [0.75, 0.15, 0.1])

    first_page_lines = 6 if page_count == 1 else FIRST_PAGE_TABLE_ROWS
    line_items, line_total = invoice_line_rows(
        first_page_lines + rows_for_section(line_pages, random_source), random_source
    )
    charge_count = rows_for_section(charge_pages, random_source) if page_count > 1 else 2
    charges, charge_total = accessorial_charge_rows(charge_count, random_source)
    fuel_total = round(line_total * 0.18, 2)
    amount_due = line_total + fuel_total + charge_total

    totals_table = ruled_table(
        [
            ["Summary", "Amount (USD)"],
            [f"Linehaul ({len(line_items)} lines)", money(line_total)],
            ["Fuel surcharge (18%, example)", money(fuel_total)],
            [f"Accessorials ({len(charges)} charges)", money(charge_total)],
            ["Total due", money(amount_due)],
        ],
        [0.7, 0.3],
        font_size=8,
    )
    first_page: list[Flowable] = [
        Paragraph(f"{title} {document_number}", styles["DocTitle"]),
        key_value_block(
            [
                ("Bill to", f"{random_source.choice(BROKER_NAMES)}\n{synthetic_address(random_source)}"),
                ("Remit to", f"{CARRIER_NAME}\n{synthetic_address(random_source)}"),
                ("Invoice date", offset_date(30)),
                ("Due date", offset_date(60)),
                ("Terms", "Net 30 (example)"),
                ("Carrier", f"{CARRIER_MC} / {CARRIER_DOT}"),
                ("Contact", f"{random_source.choice(PEOPLE_NAMES)}"),
                ("Email", "billing@example.com"),
            ]
        ),
        Spacer(1, 8),
        Paragraph("Line items", styles["Section"]),
        ruled_table([INVOICE_HEADER, *line_items[:first_page_lines]], INVOICE_COLUMNS),
    ]
    remittance = Paragraph(
        f"Please reference {document_number} with payment. Remittance details are on file (sample). "
        "This invoice is synthetic and requests no real payment.",
        styles["Small"],
    )
    if page_count == 1:
        first_page += [
            Spacer(1, 8),
            ruled_table([CHARGE_HEADER, *charges], CHARGE_COLUMNS),
            Spacer(1, 8),
            totals_table,
            Spacer(1, 6),
            remittance,
        ]
        return title, document_number, [first_page]

    pages: list[list[Flowable]] = [first_page]
    pages += paginate_table("Line items", INVOICE_HEADER, line_items[first_page_lines:], INVOICE_COLUMNS, line_pages, styles, continues_first_page=True)
    pages += paginate_table("Accessorial charges", CHARGE_HEADER, charges, CHARGE_COLUMNS, charge_pages, styles)
    pages += prose_pages("Payment terms", terms_pages, random_source, styles)
    pages.append(
        [
            Paragraph("Invoice summary", styles["Section"]),
            totals_table,
            Spacer(1, 10),
            remittance,
            Spacer(1, 10),
            Paragraph("Remittance stub", styles["Section"]),
            key_value_block(
                [
                    ("Invoice", document_number),
                    ("Amount due", f"USD {money(amount_due)}"),
                    ("Due date", offset_date(60)),
                    ("Remit to", CARRIER_NAME),
                ]
            ),
        ]
    )
    return title, document_number, pages


TRIP_HEADER = ["Trip #", "Date", "Origin", "Destination", "Loaded mi", "Empty mi", "Rate/mi", "Pay (USD)"]
TRIP_COLUMNS = [0.12, 0.12, 0.17, 0.17, 0.1, 0.09, 0.09, 0.14]
DEDUCTION_HEADER = ["Date", "Code", "Description", "Amount (USD)"]
DEDUCTION_COLUMNS = [0.16, 0.12, 0.54, 0.18]
REIMBURSEMENT_HEADER = ["Date", "Description", "Receipt #", "Amount (USD)"]
REIMBURSEMENT_COLUMNS = [0.16, 0.38, 0.28, 0.18]


def build_settlement(page_count: int, random_source: random.Random, styles: StyleSheet1) -> DocumentPages:
    document_number = synthetic_reference("STL", random_source)
    title = "Driver Settlement Statement"
    body_page_count = max(page_count - 2, 0)
    trip_pages, deduction_pages, reimbursement_pages, policy_pages = allocate_pages(
        body_page_count, [0.6, 0.2, 0.1, 0.1]
    )

    first_page_trips = 6 if page_count == 1 else FIRST_PAGE_TABLE_ROWS - 4
    trips, gross_pay = trip_rows(first_page_trips + rows_for_section(trip_pages, random_source), random_source)
    deduction_count = rows_for_section(deduction_pages, random_source) if page_count > 1 else 3
    deductions, deduction_total = deduction_rows(deduction_count, random_source)
    reimbursement_count = rows_for_section(reimbursement_pages, random_source) if page_count > 1 else 1
    reimbursements, reimbursement_total = reimbursement_rows(reimbursement_count, random_source)
    net_pay = gross_pay - deduction_total + reimbursement_total
    # Trips run two per day; the pay period must cover the last trip date.
    period_end_offset = max(13, (len(trips) - 1) // 2)

    summary_table = ruled_table(
        [
            ["Summary", "Amount (USD)"],
            [f"Gross trip pay ({len(trips)} trips)", money(gross_pay)],
            [f"Deductions ({len(deductions)})", f"-{money(deduction_total)}"],
            [f"Reimbursements ({len(reimbursements)})", money(reimbursement_total)],
            ["Net pay", money(net_pay)],
        ],
        [0.7, 0.3],
        font_size=8,
    )
    first_page: list[Flowable] = [
        Paragraph(f"{title} {document_number}", styles["DocTitle"]),
        key_value_block(
            [
                ("Driver", DRIVER_NAME),
                ("Driver ID", "DRV-0000"),
                ("Carrier", f"{CARRIER_NAME} ({CARRIER_MC})"),
                ("Truck", f"TRK-{random_source.randint(100, 999)}"),
                ("Period", f"{offset_date(0)} to {offset_date(period_end_offset)}"),
                ("Pay date", offset_date(period_end_offset + 4)),
                ("Pay type", "Per mile (example)"),
                ("Deposit", "Account on file (sample)"),
            ]
        ),
        Spacer(1, 8),
        summary_table,
        Spacer(1, 8),
        Paragraph("Trip earnings", styles["Section"]),
        ruled_table([TRIP_HEADER, *trips[:first_page_trips]], TRIP_COLUMNS),
    ]
    if page_count == 1:
        first_page += [
            Spacer(1, 8),
            Paragraph("Deductions", styles["Section"]),
            ruled_table([DEDUCTION_HEADER, *deductions], DEDUCTION_COLUMNS),
            Spacer(1, 6),
            Paragraph("Reimbursements", styles["Section"]),
            ruled_table([REIMBURSEMENT_HEADER, *reimbursements], REIMBURSEMENT_COLUMNS),
        ]
        return title, document_number, [first_page]

    pages: list[list[Flowable]] = [first_page]
    pages += paginate_table("Trip earnings", TRIP_HEADER, trips[first_page_trips:], TRIP_COLUMNS, trip_pages, styles, continues_first_page=True)
    pages += paginate_table("Deductions", DEDUCTION_HEADER, deductions, DEDUCTION_COLUMNS, deduction_pages, styles)
    pages += paginate_table(
        "Reimbursements", REIMBURSEMENT_HEADER, reimbursements, REIMBURSEMENT_COLUMNS, reimbursement_pages, styles
    )
    pages += prose_pages("Pay policy notes", policy_pages, random_source, styles)
    year_to_date_gross = gross_pay * random_source.randint(3, 20)
    pages.append(
        [
            Paragraph("Statement totals", styles["Section"]),
            summary_table,
            Spacer(1, 10),
            Paragraph("Year to date (example)", styles["Section"]),
            ruled_table(
                [
                    ["Gross", "Deductions", "Net"],
                    [
                        money(year_to_date_gross),
                        money(year_to_date_gross * 0.12),
                        money(year_to_date_gross * 0.88),
                    ],
                ],
                [0.33, 0.34, 0.33],
                font_size=8,
            ),
            Spacer(1, 10),
            Paragraph(
                "Questions about this synthetic statement go to settlements@example.com. "
                "No real driver, carrier, or payment is described.",
                styles["Small"],
            ),
        ]
    )
    return title, document_number, pages


DOCUMENT_BUILDERS: dict[str, Callable[[int, random.Random, StyleSheet1], DocumentPages]] = {
    "rate-confirmation": build_rate_confirmation,
    "bill-of-lading": build_bill_of_lading,
    "invoice": build_invoice,
    "settlement": build_settlement,
}


def label_pages(title: str, document_number: str, pages: list[list[Flowable]]) -> list[PlannedPage]:
    return [
        PlannedPage(
            flowables=flowables,
            label=PageLabel(
                document_title=title,
                document_number=document_number,
                page_in_document=page_index + 1,
                document_page_count=len(pages),
            ),
        )
        for page_index, flowables in enumerate(pages)
    ]


def plan_document(kind: str, page_count: int, seed: int, styles: StyleSheet1) -> list[PlannedPage]:
    """Lay out exactly `page_count` pages for `kind`, deterministically from `seed`."""

    random_source = random.Random(f"pdf-bench:{kind}:{page_count}:{seed}")
    if kind in DOCUMENT_BUILDERS:
        title, document_number, pages = DOCUMENT_BUILDERS[kind](page_count, random_source, styles)
        return label_pages(title, document_number, pages)

    if kind != "mixed":
        raise ValueError(f"unknown kind: {kind}")

    # A load packet: sub-documents of random kinds and lengths, back to back,
    # the way a scanned packet arrives (rate con, BOL, invoice, settlement...).
    planned: list[PlannedPage] = []
    sub_document_lengths = [1, 1, 2, 2, 3, 4, 6, 10]
    while len(planned) < page_count:
        remaining = page_count - len(planned)
        sub_kind = random_source.choice(STANDARD_KINDS)
        sub_page_count = min(random_source.choice(sub_document_lengths), remaining)
        title, document_number, pages = DOCUMENT_BUILDERS[sub_kind](sub_page_count, random_source, styles)
        planned += label_pages(title, document_number, pages)
    return planned


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def document_metadata_title(kind: str, page_count: int) -> str:
    return f"Synthetic {kind.replace('-', ' ')} ({page_count} pages)"


def render_planned_pages(planned_pages: list[PlannedPage], kind: str, output: io.BytesIO) -> None:
    total_page_count = len(planned_pages)
    is_packet = kind == "mixed"

    def draw_header_and_footer(canvas: Canvas, _document: SimpleDocTemplate) -> None:
        page_number = canvas.getPageNumber()
        label = planned_pages[page_number - 1]["label"]
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(SIDE_MARGIN, PAGE_HEIGHT - 40, f"{label['document_title']} {label['document_number']}")
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(PAGE_WIDTH - SIDE_MARGIN, PAGE_HEIGHT - 40, SYNTHETIC_BANNER)
        canvas.setLineWidth(0.5)
        canvas.line(SIDE_MARGIN, PAGE_HEIGHT - 46, PAGE_WIDTH - SIDE_MARGIN, PAGE_HEIGHT - 46)
        canvas.line(SIDE_MARGIN, 44, PAGE_WIDTH - SIDE_MARGIN, 44)
        canvas.drawString(SIDE_MARGIN, 32, f"{CARRIER_NAME} - generated by pdf-bench")
        if is_packet:
            canvas.drawCentredString(PAGE_WIDTH / 2, 32, f"Packet page {page_number} of {total_page_count}")
            page_text = f"Page {label['page_in_document']} of {label['document_page_count']}"
        else:
            page_text = f"Page {page_number} of {total_page_count}"
        canvas.drawRightString(PAGE_WIDTH - SIDE_MARGIN, 32, page_text)
        canvas.restoreState()

    document = SimpleDocTemplate(
        output,
        pagesize=LETTER,
        leftMargin=SIDE_MARGIN,
        rightMargin=SIDE_MARGIN,
        topMargin=TOP_MARGIN,
        bottomMargin=BOTTOM_MARGIN,
        title=document_metadata_title(kind, total_page_count),
        author=METADATA_AUTHOR,
        creator=METADATA_CREATOR,
        subject=METADATA_SUBJECT,
        invariant=1,
    )
    story: list[Flowable] = []
    for page_index, planned_page in enumerate(planned_pages):
        # KeepInFrame guarantees one planned page == one physical page; the
        # layouts are sized to fit, so the shrink is only a safety net.
        story.append(KeepInFrame(FRAME_WIDTH, FRAME_HEIGHT, planned_page["flowables"], mode="shrink"))
        if page_index < total_page_count - 1:
            story.append(PageBreak())
    document.build(story, onFirstPage=draw_header_and_footer, onLaterPages=draw_header_and_footer)


# The original benchmark fixture layout, unchanged so fixtures/sample.pdf keeps
# the same text and tables (and the README results stay comparable).

TERMS_PARAGRAPH = (
    "This is a synthetic document generated for benchmarking only. Carrier "
    "agrees to transport the freight described above in accordance with the "
    "stop schedule. Detention accrues after two hours at any stop at the "
    "example rate shown. Lumper fees, if any, must be pre-approved and "
    "receipted. Any claims must be filed within the example window stated in "
    "the master agreement. None of the parties, amounts, or reference numbers "
    "on this page correspond to a real shipment, company, or person."
)


def load_page_story(page_number: int, styles: StyleSheet1) -> list[Flowable]:
    load_reference = f"EXAMPLE-LOAD-{1000 + page_number}"
    story: list[Flowable] = [
        Paragraph(f"Load Confirmation {load_reference}", styles["Title"]),
        Paragraph(
            "Shipper: Example Shipper Co. &nbsp;&nbsp; Carrier: Sample Carrier LLC "
            "&nbsp;&nbsp; Broker: Demo Logistics Inc.",
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]

    stop_rows_for_page = [["Stop", "Type", "City", "Window", "Pieces", "Weight (lb)"]]
    for stop_index in range(6):
        city = SYNTHETIC_CITIES[(page_number + stop_index) % len(SYNTHETIC_CITIES)]
        stop_type = "Pickup" if stop_index == 0 else "Delivery"
        window = f"0{(stop_index % 9) + 1}:00-1{stop_index % 9}:00"
        pieces = 10 + (page_number * 3 + stop_index * 7) % 40
        weight = 1_000 + (page_number * 517 + stop_index * 911) % 9_000
        stop_rows_for_page.append(
            [str(stop_index + 1), stop_type, city, window, str(pieces), f"{weight:,}"]
        )

    stop_table = Table(stop_rows_for_page, repeatRows=1)
    stop_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    story.append(stop_table)
    story.append(Spacer(1, 12))

    rate_rows = [
        ["Line item", "Amount (USD)"],
        ["Linehaul (example)", f"{1_500 + page_number * 25:,}.00"],
        ["Fuel surcharge (example)", f"{200 + page_number * 5:,}.00"],
        ["Detention, per hour (example)", "50.00"],
    ]
    rate_table = Table(rate_rows)
    rate_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]
        )
    )
    story.append(rate_table)
    story.append(Spacer(1, 12))
    story.append(Paragraph("Terms", styles["Heading2"]))
    for _ in range(3):
        story.append(Paragraph(TERMS_PARAGRAPH, styles["Normal"]))
        story.append(Spacer(1, 6))
    return story


def render_load_confirmation(page_count: int, output: io.BytesIO) -> None:
    """The fixture layout: one load per page, no running header. Seed-independent.

    Metadata is exactly what the original fixture generator wrote (author
    `pdf-bench`, reportlab's placeholder creator), so `--fixture` reproduces the
    committed fixtures/sample.pdf byte for byte.
    """

    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(
        output,
        pagesize=LETTER,
        title="Synthetic benchmark fixture",
        author=METADATA_AUTHOR,
        invariant=1,
    )
    story: list[Flowable] = []
    for page_number in range(1, page_count + 1):
        story.extend(load_page_story(page_number, styles))
        if page_number < page_count:
            story.append(PageBreak())
    document.build(story)


def rasterize_pdf(pdf_bytes: bytes, scan_dpi: int, metadata_title: str) -> bytes:
    """Replace every page with a grayscale JPEG of itself: no text layer, like a scan.

    Needs PyMuPDF (already a dependency of the Python extractor). Expect roughly
    0.1 to 0.3 s and 100 to 250 KB per page at 150 DPI.
    """

    import pymupdf

    source = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    scanned = pymupdf.open()
    try:
        for page_index in range(source.page_count):
            source_page = source[page_index]
            pixmap = source_page.get_pixmap(dpi=scan_dpi, colorspace=pymupdf.csGRAY)
            scanned_page = scanned.new_page(width=source_page.rect.width, height=source_page.rect.height)
            scanned_page.insert_image(scanned_page.rect, stream=pixmap.tobytes("jpeg", jpg_quality=70))
        scanned.set_metadata(
            {
                "title": f"{metadata_title} [scanned]",
                "author": METADATA_AUTHOR,
                "creator": METADATA_CREATOR,
                "producer": METADATA_CREATOR,
                "subject": METADATA_SUBJECT,
                "creationDate": "D:20000101000000+00'00'",
                "modDate": "D:20000101000000+00'00'",
            }
        )
        return scanned.tobytes(garbage=3, deflate=True, no_new_id=True)
    finally:
        scanned.close()
        source.close()


def generate_pdf_bytes(kind: str, page_count: int, seed: int, scanned: bool = False, scan_dpi: int = DEFAULT_SCAN_DPI) -> bytes:
    """Return the bytes of one synthetic PDF. Pure function of its arguments."""

    if kind not in KIND_CHOICES:
        raise ValueError(f"unknown kind {kind!r}; choose from {', '.join(KIND_CHOICES)}")
    if not MIN_PAGES <= page_count <= MAX_PAGES:
        raise ValueError(f"page count must be between {MIN_PAGES} and {MAX_PAGES}, got {page_count}")

    output = io.BytesIO()
    if kind == "load-confirmation":
        render_load_confirmation(page_count, output)
    else:
        render_planned_pages(plan_document(kind, page_count, seed, build_styles()), kind, output)
    pdf_bytes = output.getvalue()
    if scanned:
        pdf_bytes = rasterize_pdf(pdf_bytes, scan_dpi, document_metadata_title(kind, page_count))
    return pdf_bytes


def write_pdf(request: GenerationRequest) -> int:
    """Generate one file; returns its size in bytes."""

    pdf_bytes = generate_pdf_bytes(
        request["kind"], request["page_count"], request["seed"], request["scanned"], request["scan_dpi"]
    )
    request["output_path"].parent.mkdir(parents=True, exist_ok=True)
    request["output_path"].write_bytes(pdf_bytes)
    return len(pdf_bytes)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def page_count_argument(raw_value: str) -> int:
    page_count = int(raw_value)
    if not MIN_PAGES <= page_count <= MAX_PAGES:
        raise argparse.ArgumentTypeError(f"--pages must be between {MIN_PAGES} and {MAX_PAGES}")
    return page_count


def positive_int_argument(raw_value: str) -> int:
    value = int(raw_value)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate synthetic, deterministic trucking paperwork PDFs.",
        epilog="Example: generate_pdfs.py --kind mixed --pages 1000 --seed 7 --out /tmp/packet-1000p.pdf",
    )
    parser.add_argument("--kind", choices=KIND_CHOICES, default="mixed", help="document type (default: mixed)")
    parser.add_argument(
        "--pages",
        type=page_count_argument,
        default=10,
        help=f"pages per document, {MIN_PAGES}-{MAX_PAGES} (default: 10)",
    )
    parser.add_argument("--seed", type=int, default=1, help="seed for the synthetic data (default: 1)")
    parser.add_argument("--count", type=positive_int_argument, default=1, help="number of documents (default: 1)")
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("--out", type=Path, help="output file (single document)")
    destination.add_argument("--out-dir", type=Path, help="output directory for a batch")
    destination.add_argument(
        "--fixture",
        action="store_true",
        help=f"rewrite the committed benchmark fixture ({FIXTURE_PATH.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--scanned",
        action="store_true",
        help="rasterize pages to images with no text layer (exercises the OCR path; slow and large)",
    )
    parser.add_argument(
        "--scan-dpi", type=positive_int_argument, default=DEFAULT_SCAN_DPI, help="DPI for --scanned (default: 150)"
    )
    return parser


def requests_from_arguments(arguments: argparse.Namespace, parser: argparse.ArgumentParser) -> list[GenerationRequest]:
    if arguments.fixture:
        return [
            GenerationRequest(
                kind="load-confirmation",
                page_count=FIXTURE_PAGE_COUNT,
                seed=0,
                scanned=False,
                scan_dpi=DEFAULT_SCAN_DPI,
                output_path=FIXTURE_PATH,
            )
        ]
    if arguments.out is not None and arguments.count != 1:
        parser.error("--out writes one file; use --out-dir with --count")
    if arguments.out is None and arguments.out_dir is None:
        parser.error("one of --out, --out-dir, or --fixture is required")

    scanned_suffix = "-scanned" if arguments.scanned else ""
    requests: list[GenerationRequest] = []
    for document_index in range(arguments.count):
        if arguments.out is not None:
            output_path = arguments.out
        else:
            file_name = (
                f"{arguments.kind}-{arguments.pages:04d}p-seed{arguments.seed}-{document_index + 1:03d}{scanned_suffix}.pdf"
            )
            output_path = arguments.out_dir / file_name
        requests.append(
            GenerationRequest(
                kind=arguments.kind,
                page_count=arguments.pages,
                # Each document in a batch gets its own seed so they differ.
                seed=arguments.seed + document_index,
                scanned=arguments.scanned,
                scan_dpi=arguments.scan_dpi,
                output_path=output_path,
            )
        )
    return requests


def main(argv: list[str] | None = None) -> None:
    parser = build_argument_parser()
    arguments = parser.parse_args(argv)
    for request in requests_from_arguments(arguments, parser):
        size_bytes = write_pdf(request)
        print(f"wrote {request['output_path']} ({size_bytes} bytes, {request['page_count']} pages, {request['kind']})")


if __name__ == "__main__":
    main()
