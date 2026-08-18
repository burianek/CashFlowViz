"""Command-line entry point: FROM/AMOUNT/DATE XLSX in, multi-column Sankey SVG out."""

from __future__ import annotations

import argparse
import sys

from .data import build_graph, read_flow_entries
from .render import render_png, render_svg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cashflowviz.sankey",
        description="Render a FROM/AMOUNT/DATE cashflow XLSX as a multi-column Sankey diagram (SVG/PNG).",
    )
    parser.add_argument("input", help="Path to the input .xlsx file")
    parser.add_argument(
        "-o", "--out", default="cashflow-sankey.svg", help="Output SVG path (default: cashflow-sankey.svg)"
    )
    parser.add_argument(
        "--png", default=None, help="Also export a PNG to this path (requires cairosvg)"
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help=(
            "Minimum graph width in px (grows past it to fit columns/lanes if the content needs "
            "more room). Governs the SVG's actual width; --png follows it proportionally (2x, for "
            "a crisp retina-quality image)."
        ),
    )
    parser.add_argument(
        "--height", type=int, default=780, help="Minimum SVG height in px (grows to fit stacked nodes)"
    )
    parser.add_argument("--title", default="Cash Flow", help="Chart title")
    parser.add_argument("--currency", default="", help="Currency label appended to amounts")
    parser.add_argument(
        "--by-date",
        action="store_true",
        help=(
            "Position income/expense nodes by their actual DATE on a real timeline instead of "
            "grouping everything from a month under one fixed column. Month-milestone hubs stay "
            "anchored between their own month's entries and the next month's."
        ),
    )
    parser.add_argument(
        "--merge-same-month",
        action="store_true",
        help=(
            "Combine FROM rows that share a name within the same calendar month into a single "
            "node (amounts summed) instead of auto-numbering each one. Only valid in the default "
            "categorical column layout — incompatible with --by-date, where each row keeps its "
            "own point on the timeline."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.merge_same_month and args.by_date:
        parser.error("--merge-same-month cannot be used with --by-date")

    entries = read_flow_entries(args.input)
    graph = build_graph(entries, merge_same_month=args.merge_same_month)
    render_svg(
        graph,
        args.out,
        width=args.width,
        height=args.height,
        title=args.title,
        currency=args.currency,
        date_positioned=args.by_date,
    )
    print(f"Wrote {args.out}")

    if args.png:
        render_png(args.out, args.png)
        print(f"Wrote {args.png}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
