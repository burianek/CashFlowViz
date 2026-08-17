"""Command-line entry point: XLSX in, cash flow diagram SVG (and optional PNG) out."""

from __future__ import annotations

import argparse
import sys

from .data import read_entries
from .render import render_png, render_svg


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cashflowviz",
        description="Render a company cashflow XLSX as a per-asset cash flow diagram (SVG/PNG).",
    )
    parser.add_argument("input", help="Path to the input .xlsx file")
    parser.add_argument(
        "-o", "--out", default="cashflow.svg", help="Output SVG path (default: cashflow.svg)"
    )
    parser.add_argument(
        "--png", default=None, help="Also export a PNG to this path (requires cairosvg)"
    )
    parser.add_argument("--width", type=int, default=1200, help="SVG width in px")
    parser.add_argument(
        "--height", type=int, default=820, help="Minimum SVG height in px (grows to fit labels)"
    )
    parser.add_argument("--title", default="Cash Flow Balance", help="Chart title")
    parser.add_argument("--currency", default="", help="Currency label appended to amounts")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    entries = read_entries(args.input)
    render_svg(
        entries,
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
