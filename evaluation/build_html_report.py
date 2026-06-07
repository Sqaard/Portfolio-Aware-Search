"""Build a compact static HTML report for retrieval experiments."""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path
from typing import Optional, Union


def read_csv(path: Union[str, Path]) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def fmt(value: object) -> str:
    text = str(value)
    try:
        number = float(text)
    except ValueError:
        return html.escape(text)
    if 0 <= number <= 1:
        return f"{number:.3f}"
    return f"{number:.2f}" if number % 1 else str(int(number))


def table_html(title: str, rows: list[dict[str, str]]) -> str:
    if not rows:
        return f"<section><h2>{html.escape(title)}</h2><p>No rows.</p></section>"
    headers = list(rows[0].keys())
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{fmt(row.get(header, ''))}</td>" for header in headers)
        body_rows.append(f"<tr>{cells}</tr>")
    body = "\n".join(body_rows)
    return (
        f"<section><h2>{html.escape(title)}</h2>"
        f"<div class=\"table-wrap\"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
        "</section>"
    )


def build_report(metrics_rows: list[dict[str, str]], diagnostics_rows: list[dict[str, str]], title: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #1f2937; background: #f8fafc; overflow-x: hidden; }}
    h1 {{ margin-bottom: 4px; }}
    .subtitle {{ color: #475569; margin-top: 0; }}
    section {{ margin-top: 28px; }}
    .table-wrap {{ overflow-x: auto; background: white; border: 1px solid #d7dee8; }}
    table {{ border-collapse: collapse; width: 100%; min-width: 760px; background: white; }}
    th, td {{ border-bottom: 1px solid #e5eaf0; padding: 9px 10px; text-align: left; font-size: 13px; white-space: nowrap; }}
    th {{ background: #e8eef6; font-weight: 700; }}
    tr:hover td {{ background: #f3f7fb; }}
    code {{ background: #e8eef6; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p class="subtitle">Primary metric: <code>nDCG@10</code>. Diagnostics track causality, duplicate concentration, and portfolio coverage.</p>
  {table_html("IR Metrics By Method", metrics_rows)}
  {table_html("Retrieval Diagnostics By Method", diagnostics_rows)}
</body>
</html>
"""


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build static HTML retrieval report.")
    parser.add_argument("--metrics", required=True)
    parser.add_argument("--diagnostics", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", default="FinPortfolio IR Retrieval Report")
    args = parser.parse_args(argv)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_report(read_csv(args.metrics), read_csv(args.diagnostics), args.title),
        encoding="utf-8",
    )
    print(f"Wrote HTML report to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
