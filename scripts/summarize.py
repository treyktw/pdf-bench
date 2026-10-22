"""Summarize benchmark JSON lines (one per run) into a Markdown table.

    python scripts/summarize.py results.jsonl              # per-engine table
    python scripts/summarize.py --scaling results.jsonl    # one row per document size

In scaling mode `scripts/benchmark.sh --scaling` writes a marker line
(`{"marker": "file", "path": ..., "pages": ..., "fileBytes": ...}`) before the
runs for each file; runs are grouped under the most recent marker.

Standard library only, so it runs without the benchmark venv.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import TypedDict


class BenchmarkRun(TypedDict):
    engine: str
    elapsedMs: float
    textChars: int
    tableCount: int
    ocrUsed: bool


class ScalingGroup(TypedDict):
    path: str
    pages: int
    file_bytes: int
    runs_by_engine: dict[str, list[BenchmarkRun]]


def read_json_lines(results_path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in results_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("{"):
            records.append(json.loads(line))
    return records


def format_file_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / 1024:.0f} KB"


def summarize_engines(records: list[dict[str, object]]) -> None:
    runs_by_engine: dict[str, list[dict[str, object]]] = {}
    for record in records:
        if "engine" in record:
            runs_by_engine.setdefault(str(record["engine"]), []).append(record)

    print("| Engine | Runs | Median ms | Min ms | Max ms | Text chars | Tables |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for engine, runs in runs_by_engine.items():
        elapsed = [float(str(run["elapsedMs"])) for run in runs]
        print(
            f"| {engine} | {len(runs)} | {statistics.median(elapsed):.2f} "
            f"| {min(elapsed):.2f} | {max(elapsed):.2f} "
            f"| {runs[-1]['textChars']} | {runs[-1]['tableCount']} |"
        )


def group_scaling_runs(records: list[dict[str, object]]) -> list[ScalingGroup]:
    groups: list[ScalingGroup] = []
    for record in records:
        if record.get("marker") == "file":
            groups.append(
                ScalingGroup(
                    path=str(record["path"]),
                    pages=int(str(record["pages"])),
                    file_bytes=int(str(record["fileBytes"])),
                    runs_by_engine={},
                )
            )
        elif "engine" in record:
            if not groups:
                raise SystemExit("scaling results must start with a file marker line")
            run = BenchmarkRun(
                engine=str(record["engine"]),
                elapsedMs=float(str(record["elapsedMs"])),
                textChars=int(str(record["textChars"])),
                tableCount=int(str(record["tableCount"])),
                ocrUsed=bool(record["ocrUsed"]),
            )
            groups[-1]["runs_by_engine"].setdefault(run["engine"], []).append(run)
    return sorted(groups, key=lambda group: group["pages"])


def summarize_scaling(records: list[dict[str, object]]) -> None:
    print(
        "| Pages | File size | Runs | Python median ms | Rust median ms "
        "| Python ms/page | Rust ms/page | Chars (Python) | Chars (Rust) | Tables (Python) |"
    )
    print("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for group in group_scaling_runs(records):
        python_runs = group["runs_by_engine"].get("python", [])
        rust_runs = group["runs_by_engine"].get("rust", [])
        python_median = statistics.median(run["elapsedMs"] for run in python_runs) if python_runs else None
        rust_median = statistics.median(run["elapsedMs"] for run in rust_runs) if rust_runs else None
        run_count = max(len(python_runs), len(rust_runs))

        def cell(value: float | None, digits: int = 1) -> str:
            return "n/a" if value is None else f"{value:,.{digits}f}"

        def count_cell(runs: list[BenchmarkRun], field: str) -> str:
            return "n/a" if not runs else f"{int(runs[-1][field]):,}"  # type: ignore[literal-required]

        python_per_page = python_median / group["pages"] if python_median is not None else None
        rust_per_page = rust_median / group["pages"] if rust_median is not None else None
        print(
            f"| {group['pages']:,} | {format_file_size(group['file_bytes'])} | {run_count} "
            f"| {cell(python_median)} | {cell(rust_median)} "
            f"| {cell(python_per_page)} | {cell(rust_per_page, 2)} "
            f"| {count_cell(python_runs, 'textChars')} | {count_cell(rust_runs, 'textChars')} "
            f"| {count_cell(python_runs, 'tableCount')} |"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize benchmark JSON lines into a Markdown table.")
    parser.add_argument("results", type=Path, help="file of JSON lines written by scripts/benchmark.sh")
    parser.add_argument("--scaling", action="store_true", help="one row per document (needs file marker lines)")
    arguments = parser.parse_args()

    records = read_json_lines(arguments.results)
    if arguments.scaling:
        summarize_scaling(records)
    else:
        summarize_engines(records)


if __name__ == "__main__":
    main()
