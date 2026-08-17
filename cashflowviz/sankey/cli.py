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
    parser.add_argument("--width", type=int, default=None, help="Minimum SVG width in px (grows to fit columns)")
    parser.add_argument(
        "--height", type=int, default=780, help="Minimum SVG height in px (grows to fit stacked nodes)"
    )
    parser.add_argument("--title", default="Cash Flow", help="Chart title")
    parser.add_argument("--currency", default="", help="Currency label appended to amounts")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    entries = read_flow_entries(args.input)
    graph = build_graph(entries)
    render_svg(
        graph,
        args.out,
        width=args.width,
        height=args.height,
        title=args.title,
        currency=args.currency,
    )
    print(f"Wrote {args.out}")

    if args.png:
        render_png(args.out, args.png)
        print(f"Wrote {args.png}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
