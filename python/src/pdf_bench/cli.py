from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

from .extractor import extract_pdf_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract text from a local PDF.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--max-pages", type=int, default=500)
    args = parser.parse_args()

    started = perf_counter()
    data = args.pdf.read_bytes()
    result = extract_pdf_bytes(
        data,
        allow_ocr=not args.no_ocr,
        max_pdf_pages=args.max_pages,
    )
    elapsed_ms = (perf_counter() - started) * 1000

    print(
        json.dumps(
            {
                "engine": "python",
                "elapsedMs": round(elapsed_ms, 2),
                "textChars": len(result.text),
                "tableCount": len(result.tables),
                "ocrUsed": result.ocr_used,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
