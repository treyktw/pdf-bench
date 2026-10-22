#!/usr/bin/env bash
# Run both extractors against the same PDF(s) and print a Markdown summary.
#
#   scripts/benchmark.sh                          # fixtures/sample.pdf, RUNS each
#   scripts/benchmark.sh /path/to/other.pdf       # any single PDF
#   scripts/benchmark.sh --scaling                # every PDF in fixtures/generated/
#   scripts/benchmark.sh --scaling a.pdf b.pdf    # or an explicit list / directory
#
# Environment:
#   RUNS=5             runs per engine per file
#   RUNS_LARGE=3       runs per engine for files with >= LARGE_PAGES pages (scaling mode)
#   LARGE_PAGES=500
#   RESULTS_FILE=path  keep the raw JSON lines (one per run) instead of a temp file
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNS="${RUNS:-5}"
RUNS_LARGE="${RUNS_LARGE:-3}"
LARGE_PAGES="${LARGE_PAGES:-500}"

MODE="single"
if [[ "${1:-}" == "--scaling" ]]; then
  MODE="scaling"
  shift
fi

# Prefer the repo-local venv from `make setup`; fall back to whatever python is on PATH.
if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x "${ROOT_DIR}/python/.venv/bin/python" ]]; then
    PYTHON="${ROOT_DIR}/python/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

# Use the PDFium from scripts/fetch-pdfium.sh if the caller did not point at one.
if [[ -z "${PDFIUM_DYNAMIC_LIB_PATH:-}" && -d "${ROOT_DIR}/.pdfium/lib" ]]; then
  export PDFIUM_DYNAMIC_LIB_PATH="${ROOT_DIR}/.pdfium/lib"
fi

# Resolve the list of PDFs to benchmark.
PDF_PATHS=()
if [[ "$MODE" == "single" ]]; then
  PDF_PATHS=("${1:-${ROOT_DIR}/fixtures/sample.pdf}")
else
  [[ $# -eq 0 ]] && set -- "${ROOT_DIR}/fixtures/generated"
  for target in "$@"; do
    if [[ -d "$target" ]]; then
      # Generated names are zero-padded (mixed-0010p.pdf), so glob order is page order.
      for pdf in "$target"/*.pdf; do
        [[ -f "$pdf" ]] && PDF_PATHS+=("$pdf")
      done
    else
      PDF_PATHS+=("$target")
    fi
  done
  if [[ ${#PDF_PATHS[@]} -eq 0 ]]; then
    echo "No PDFs to benchmark. Generate the size ladder with: make fixtures-long" >&2
    exit 1
  fi
fi

for pdf in "${PDF_PATHS[@]}"; do
  if [[ ! -f "$pdf" ]]; then
    echo "Missing PDF: $pdf" >&2
    echo "Generate one with: make fixture / make fixtures-long   (or pass a path)" >&2
    exit 1
  fi
done

if [[ -n "${RESULTS_FILE:-}" ]]; then
  RESULTS="$RESULTS_FILE"
  : >"$RESULTS"
else
  RESULTS="$(mktemp)"
  trap 'rm -f "$RESULTS"' EXIT
fi

pushd "${ROOT_DIR}/rust" >/dev/null
cargo build --release --quiet
popd >/dev/null
RUST_BIN="${ROOT_DIR}/rust/target/release/pdf-extract-rust"

page_count() {
  "$PYTHON" -c 'import sys, pymupdf; print(len(pymupdf.open(sys.argv[1])))' "$1"
}

for pdf in "${PDF_PATHS[@]}"; do
  pdf="$(cd "$(dirname "$pdf")" && pwd)/$(basename "$pdf")"
  runs="$RUNS"
  if [[ "$MODE" == "scaling" ]]; then
    pages="$(page_count "$pdf")"
    file_bytes="$(wc -c <"$pdf" | tr -d ' ')"
    if (( pages >= LARGE_PAGES )); then
      runs="$RUNS_LARGE"
    fi
    # Marker line: summarize.py --scaling groups the runs that follow under this file.
    printf '{"marker": "file", "path": "%s", "pages": %s, "fileBytes": %s}\n' \
      "$(basename "$pdf")" "$pages" "$file_bytes" >>"$RESULTS"
    echo "Benchmarking $(basename "$pdf") ($pages pages, $file_bytes bytes, $runs runs each)"
  else
    echo "Benchmarking $pdf ($runs runs each)"
  fi
  echo

  pushd "${ROOT_DIR}/python" >/dev/null
  for _ in $(seq 1 "$runs"); do
    "$PYTHON" -m pdf_bench.cli "$pdf" --no-ocr | tee -a "$RESULTS"
  done
  popd >/dev/null

  for _ in $(seq 1 "$runs"); do
    "$RUST_BIN" "$pdf" --no-ocr | tee -a "$RESULTS"
  done
  echo
done

if [[ "$MODE" == "scaling" ]]; then
  python3 "${ROOT_DIR}/scripts/summarize.py" --scaling "$RESULTS"
else
  python3 "${ROOT_DIR}/scripts/summarize.py" "$RESULTS"
fi
