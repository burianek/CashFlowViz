# CashFlowViz

A small, open-source Python utility that turns a simple company cashflow
spreadsheet into a **cash flow diagram**: a central "balance trunk" whose
thickness tracks the account balance over time, with every transaction drawn
as its own colored ribbon that merges in (money in, thickening the trunk) or
peels off (money out, thinning it) at its date — in the spirit of
[Finary's budget calculator](https://finary.com/en/calculateur-de-budget),
adapted from a static category flow into a per-asset flow over time.

Output is a standalone vector picture (SVG), with optional PNG export.

![Example cash flow diagram](examples/cashflow_example.svg)

## Input format

An `.xlsx` file with three columns, in any column order (matched by header
name, case-insensitive):

| Assets   | Date       | IN/OUT  |
|----------|------------|---------|
| STATUS   | 2026-08-17 | 1000000 |
| Proj 1   | 2026-08-20 |  100000 |
| Tomáš N  | 2026-08-26 | -100000 |
| Jan B    | 2026-09-08 | -140000 |

- **Assets**: name of the project/person the line item belongs to. Each
  distinct asset gets its own ribbon color (fixed, colorblind-safe
  categorical palette), reused consistently across the chart and legend.
- **Date**: the day the amount is booked — this drives the x-axis and the
  point where each asset's ribbon meets the trunk.
- **IN/OUT**: positive = money coming in (ribbon merges in from above, trunk
  grows); negative = money going out (ribbon peels off below, trunk shrinks).
- A row whose Assets value is `STATUS` sets the balance to an **absolute**
  value on that date (an opening balance / bank statement checkpoint)
  instead of adding a delta, and doesn't get its own ribbon.

The trunk holds flat between transactions and eases smoothly through each
one, so its shape reflects exactly what the data says happened — no
day-by-day interpolation.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

PNG export is optional and needs `cairosvg`, which needs the native `cairo`
library:

```bash
pip install -r requirements-optional.txt

# macOS
brew install cairo
# Homebrew's lib path isn't always on the dynamic loader's search path;
# if PNG export fails with "no library called cairo-2 was found", run:
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib

# Debian/Ubuntu
sudo apt-get install libcairo2
```

## Usage

```bash
python -m cashflowviz InputExample.xlsx -o cashflow.svg
python -m cashflowviz InputExample.xlsx -o cashflow.svg --png cashflow.png
```

Options:

| Flag | Default | Description |
|---|---|---|
| `-o, --out` | `cashflow.svg` | Output SVG path |
| `--png` | *(none)* | Also export a PNG to this path |
| `--width` | `1200` | SVG width in px |
| `--height` | `820` | Minimum SVG height in px — grows automatically to fit stacked labels when many transactions land close together |
| `--title` | `Cash Flow Balance` | Chart title |
| `--currency` | *(empty)* | Currency label appended to amounts, e.g. `Kč` |

## Design

- `cashflowviz/data.py` — reads the workbook and orders entries chronologically
  into `Transition`s, each carrying the balance level before/after it.
- `cashflowviz/render.py` — draws the flow diagram as SVG:
  - the **trunk** (balance over time) as a smoothed step area, split into
    green/red fills above/below zero with exact zero-crossing interpolation;
  - one **ribbon** per transaction (Sankey-style bezier link, width
    proportional to the amount) connecting a labeled pill to the trunk;
  - pills auto-stack into rows to avoid overlap when transactions cluster,
    and clamp horizontally so they never run past the chart edge;
  - a fixed, colorblind-validated categorical palette assigns one color per
    asset (see the `dataviz` design skill's reference palette), with overflow
    past 8 distinct assets falling back to a muted neutral color.
  - PNG export rasterizes the SVG via `cairosvg`.
- `cashflowviz/cli.py` — the `python -m cashflowviz` command line interface.

Everything is plain Python + [svgwrite](https://github.com/mozman/svgwrite)
(open source, MIT-ish license) — no Blender dependency required to generate
the graph. A Blender/Grease-Pencil front end was considered, since Blender
has no reliable built-in path from Grease Pencil strokes to a standalone SVG
file; this standalone script is the more robust way to hit the "clean vector
file" deliverable. Wrapping this as a Blender addon (e.g. driving Grease
Pencil in 2D Animation mode from the same `data.py`/computed transitions)
remains a natural follow-up if a Blender-native workflow is wanted later.

## License

MIT — see [LICENSE](LICENSE).
