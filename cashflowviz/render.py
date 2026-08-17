"""Render balance transitions as a Sankey-style cash flow diagram in SVG.

A central "trunk" band tracks the account balance over time (its thickness is
the balance magnitude). Every transaction is drawn as its own colored ribbon
that either merges into the trunk (money in, thickening it) or peels off the
trunk (money out, thinning it) at the transaction's date, connecting to a
labeled pill above (IN) or below (OUT) the trunk.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import svgwrite

from .data import Transition

# Categorical palette (fixed order), from the validated dataviz reference palette.
ASSET_PALETTE = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
]
OVERFLOW_COLOR = "#898781"  # muted, for the 9th+ distinct asset

SURFACE_COLOR = "#fcfcfb"
GRID_COLOR = "#e1e0d9"
BASELINE_COLOR = "#c3c2b7"
PRIMARY_INK = "#0b0b0b"
SECONDARY_INK = "#52514e"
MUTED_INK = "#898781"
TRUNK_FILL = "#c7ccd6"
TRUNK_STROKE = "#5b6472"
TRUNK_NEGATIVE_FILL = "#f3c9c8"
TRUNK_NEGATIVE_STROKE = "#b64a48"

FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif'

MIN_RIBBON_PX = 6.0
STATUS_MARKER_COLOR = "#5b6472"
PILL_H = 24
PILL_PAD_X = 10
ROW_GAP_Y = 12
ROW_GAP_X = 14
STEM_GAP = 26  # vertical gap between a pill row and the trunk / next row


@dataclass
class _Margins:
    top: int = 56
    right: int = 40
    bottom: int = 54
    left: int = 100


@dataclass
class _Placement:
    transition: Transition
    x: float = 0.0
    half_width: float = 18.0
    color: str = "#000000"
    row: int = 0
    attach_top: float = 0.0
    attach_bot: float = 0.0
    label_w: float = 0.0
    pill_x: float = 0.0


def _nice_step(rough_step: float) -> float:
    if rough_step <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(rough_step))
    residual = rough_step / magnitude
    nice = 1 if residual < 1.5 else 2 if residual < 3 else 5 if residual < 7 else 10
    return nice * magnitude


def _y_ticks(min_val: float, max_val: float, target_count: int = 5) -> list[float]:
    if min_val == max_val:
        min_val -= 1
        max_val += 1
    step = _nice_step((max_val - min_val) / target_count)
    start = math.floor(min_val / step) * step
    ticks = []
    v = start
    while v <= max_val + step * 0.001:
        ticks.append(round(v, 6))
        v += step
    return ticks


def _format_amount(value: float, currency: str, signed: bool = False) -> str:
    text = f"{abs(value):,.0f}".replace(",", " ")
    sign = "+" if value > 0 else "-" if value < 0 else ""
    if signed:
        text = f"{sign}{text}"
    elif value < 0:
        text = f"-{text}"
    if currency:
        text = f"{text} {currency}"
    return text


def _month_ticks(start: dt.date, end: dt.date) -> list[dt.date]:
    ticks = []
    cur = start.replace(day=1)
    while cur <= end:
        ticks.append(cur)
        cur = cur.replace(year=cur.year + 1, month=1) if cur.month == 12 else cur.replace(month=cur.month + 1)
    if not ticks or ticks[0] != start:
        ticks.insert(0, start)
    return ticks


def _estimate_text_width(text: str, font_px: float = 12.0) -> float:
    return len(text) * font_px * 0.58


def _blend(hex_color: str, with_hex: str, ratio: float) -> str:
    """Blend hex_color toward with_hex; ratio=1 returns with_hex."""
    a = tuple(int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(with_hex[i : i + 2], 16) for i in (1, 3, 5))
    mixed = tuple(round(a[i] + (b[i] - a[i]) * ratio) for i in range(3))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _cubic_bezier(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    mt = 1 - t
    x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
    y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
    return x, y


def _sankey_link_path(x0, y0_top, y0_bot, x1, y1_top, y1_bot) -> str:
    xm = (x0 + x1) / 2
    return (
        f"M {x0:.2f},{y0_top:.2f} "
        f"C {xm:.2f},{y0_top:.2f} {xm:.2f},{y1_top:.2f} {x1:.2f},{y1_top:.2f} "
        f"L {x1:.2f},{y1_bot:.2f} "
        f"C {xm:.2f},{y1_bot:.2f} {xm:.2f},{y0_bot:.2f} {x0:.2f},{y0_bot:.2f} Z"
    )


def _assign_rows(intervals: list[tuple[float, float]]) -> tuple[list[int], int]:
    """Greedy interval scheduling: pack (left, right) x-ranges into the fewest rows."""
    row_right: list[float] = []
    row_of: list[int] = []
    for left, right in intervals:
        placed = False
        for i, r in enumerate(row_right):
            if left >= r + ROW_GAP_X:
                row_right[i] = right
                row_of.append(i)
                placed = True
                break
        if not placed:
            row_right.append(right)
            row_of.append(len(row_right) - 1)
    return row_of, max(len(row_right), 1)


def render_svg(
    entries,
    out_path: str,
    width: int = 1200,
    height: int = 820,
    title: str = "Cash Flow Balance",
    currency: str = "",
) -> str:
    from .data import compute_transitions

    if not entries:
        raise ValueError("No entries to render")

    transitions = compute_transitions(entries)
    margins = _Margins()
    plot_w = width - margins.left - margins.right
    x_left = margins.left
    x_right = width - margins.right

    start_date = transitions[0].entry.date
    end_date = max(t.entry.date for t in transitions)
    date_span = max((end_date - start_date).days, 1)

    def x_of(date: dt.date) -> float:
        return x_left + (date - start_date).days / date_span * plot_w

    # --- assign colors -------------------------------------------------
    color_of_asset: dict[str, str] = {}
    for t in transitions:
        if t.entry.is_status:
            continue
        if t.entry.asset not in color_of_asset:
            idx = len(color_of_asset)
            color_of_asset[t.entry.asset] = (
                ASSET_PALETTE[idx] if idx < len(ASSET_PALETTE) else OVERFLOW_COLOR
            )

    # --- x placement with same-day spreading ----------------------------
    placements = [_Placement(transition=t) for t in transitions]
    by_date: dict[dt.date, list[_Placement]] = {}
    for p in placements:
        by_date.setdefault(p.transition.entry.date, []).append(p)

    unique_dates = sorted(by_date.keys())
    nominal_x = {d: x_of(d) for d in unique_dates}
    for i, d in enumerate(unique_dates):
        prev_gap = nominal_x[d] - nominal_x[unique_dates[i - 1]] if i > 0 else plot_w
        next_gap = nominal_x[unique_dates[i + 1]] - nominal_x[d] if i + 1 < len(unique_dates) else plot_w
        window = max(min(40.0, 0.8 * prev_gap, 0.8 * next_gap), 0.0)
        group = by_date[d]
        n = len(group)
        for j, p in enumerate(group):
            if n == 1:
                p.x = nominal_x[d]
            else:
                p.x = nominal_x[d] - window / 2 + window * (j + 0.5) / n

    for i, p in enumerate(placements):
        prev_x = placements[i - 1].x if i > 0 else x_left
        next_x = placements[i + 1].x if i + 1 < len(placements) else x_right
        p.half_width = max(min(18.0, 0.4 * (p.x - prev_x), 0.4 * (next_x - p.x)), 2.0)
        p.color = color_of_asset.get(p.transition.entry.asset, STATUS_MARKER_COLOR)

    # --- vertical scale ---------------------------------------------------
    all_levels = [t.level_before for t in transitions] + [t.level_after for t in transitions]
    min_level = min(all_levels + [0.0])
    max_level = max(all_levels + [0.0])
    level_pad = (max_level - min_level) * 0.12 or max(abs(max_level), 1.0) * 0.12
    min_level -= level_pad
    max_level += level_pad

    # --- row layout for IN / OUT pills ------------------------------------
    in_items, out_items = [], []
    for p in placements:
        e = p.transition.entry
        if e.is_status:
            continue
        label = f"{e.asset}: {_format_amount(e.amount, currency, signed=True)}"
        w = max(_estimate_text_width(label) + PILL_PAD_X * 2, MIN_RIBBON_PX)
        p.label_w = w
        p.pill_x = min(max(p.x, x_left + w / 2), x_right - w / 2)
        (in_items if e.amount >= 0 else out_items).append((p, p.pill_x - w / 2, p.pill_x + w / 2, w, label))

    in_rows_idx, in_row_count = _assign_rows([(it[1], it[2]) for it in in_items])
    out_rows_idx, out_row_count = _assign_rows([(it[1], it[2]) for it in out_items])
    for (p, *_rest), row in zip(in_items, in_rows_idx):
        p.row = row
    for (p, *_rest), row in zip(out_items, out_rows_idx):
        p.row = row

    in_h = STEM_GAP + in_row_count * PILL_H + max(in_row_count - 1, 0) * ROW_GAP_Y
    out_h = STEM_GAP + out_row_count * PILL_H + max(out_row_count - 1, 0) * ROW_GAP_Y

    legend_row_h = 18
    legend_row_w = 0.0
    legend_rows = 1
    for asset in color_of_asset:
        item_w = 16 + _estimate_text_width(asset, 11) + 22
        if legend_row_w > 0 and legend_row_w + item_w > plot_w:
            legend_rows += 1
            legend_row_w = item_w
        else:
            legend_row_w += item_w
    legend_h = legend_rows * legend_row_h + 10
    axis_h = 34
    trunk_h = 230

    title_y = margins.top
    legend_y = title_y + 18
    in_top = legend_y + legend_h
    trunk_top = in_top + in_h
    trunk_bottom = trunk_top + trunk_h
    out_bottom = trunk_bottom + out_h
    axis_y = out_bottom + axis_h

    total_height = max(axis_y + margins.bottom, height)

    def y_of(level: float) -> float:
        return trunk_top + (max_level - level) / (max_level - min_level) * trunk_h

    zero_y = y_of(0.0)

    # --- boundary construction (flat segments + smoothed S-curve ramps) ---
    # sample_points: dense (x, y) along the whole trunk boundary, for the area fill
    sample_points: list[tuple[float, float]] = []
    path_cmds: list[str] = []

    first = placements[0]
    if first.transition.entry.is_status:
        cursor_x = x_left
        cursor_level = first.transition.level_after
        rest = placements[1:]
    else:
        cursor_x = x_left
        cursor_level = first.transition.level_before
        rest = placements

    path_cmds.append(f"M {cursor_x:.2f},{y_of(cursor_level):.2f}")
    sample_points.append((cursor_x, y_of(cursor_level)))

    for p in rest:
        ramp_start_x = max(cursor_x, p.x - p.half_width)
        ramp_end_x = p.x + p.half_width
        y_before = y_of(cursor_level)
        y_after = y_of(p.transition.level_after)

        if ramp_start_x > cursor_x:
            path_cmds.append(f"L {ramp_start_x:.2f},{y_before:.2f}")
            sample_points.append((ramp_start_x, y_before))

        xm = (ramp_start_x + ramp_end_x) / 2
        p0 = (ramp_start_x, y_before)
        p1 = (xm, y_before)
        p2 = (xm, y_after)
        p3 = (ramp_end_x, y_after)
        path_cmds.append(f"C {p1[0]:.2f},{p1[1]:.2f} {p2[0]:.2f},{p2[1]:.2f} {p3[0]:.2f},{p3[1]:.2f}")
        for step in range(1, 13):
            sample_points.append(_cubic_bezier(p0, p1, p2, p3, step / 12))

        p.attach_top = min(y_before, y_after)
        p.attach_bot = max(y_before, y_after)
        cursor_x = ramp_end_x
        cursor_level = p.transition.level_after

    if cursor_x < x_right:
        y_final = y_of(cursor_level)
        path_cmds.append(f"L {x_right:.2f},{y_final:.2f}")
        sample_points.append((x_right, y_final))

    boundary_d = " ".join(path_cmds)

    # split sample_points into positive/negative filled segments (zero-crossing aware)
    fill_segments: list[tuple[list[tuple[float, float]], bool]] = []
    current_seg = [sample_points[0]]
    for (x0, y0), (x1, y1) in zip(sample_points, sample_points[1:]):
        same_side = (y0 <= zero_y) == (y1 <= zero_y)
        if same_side or y0 == zero_y or y1 == zero_y:
            current_seg.append((x1, y1))
        else:
            t = (zero_y - y0) / (y1 - y0)
            cross_x = x0 + t * (x1 - x0)
            current_seg.append((cross_x, zero_y))
            fill_segments.append((current_seg, sum(zero_y - p[1] for p in current_seg) >= 0))
            current_seg = [(cross_x, zero_y), (x1, y1)]
    fill_segments.append((current_seg, sum(zero_y - p[1] for p in current_seg) >= 0))

    # ======================================================================
    dwg = svgwrite.Drawing(out_path, size=(width, total_height), profile="full")
    dwg.add(dwg.rect(insert=(0, 0), size=(width, total_height), fill=SURFACE_COLOR))

    dwg.add(
        dwg.text(
            title,
            insert=(margins.left, title_y),
            font_size="20px",
            font_family=FONT_FAMILY,
            font_weight="600",
            fill=PRIMARY_INK,
        )
    )

    # legend (wraps to a new row when it would run past the right margin)
    lx = margins.left
    ly = legend_y
    for asset, color in color_of_asset.items():
        item_w = 16 + _estimate_text_width(asset, 11) + 22
        if lx > margins.left and lx + item_w > x_right:
            lx = margins.left
            ly += legend_row_h
        dwg.add(dwg.rect(insert=(lx, ly - 10), size=(11, 11), rx=2, ry=2, fill=color))
        dwg.add(
            dwg.text(
                asset,
                insert=(lx + 16, ly - 1),
                font_size="11px",
                font_family=FONT_FAMILY,
                fill=SECONDARY_INK,
            )
        )
        lx += item_w

    # month gridlines spanning the whole chart height
    for m in _month_ticks(start_date, end_date):
        mx = x_of(m)
        dwg.add(dwg.line(start=(mx, in_top), end=(mx, out_bottom), stroke=GRID_COLOR, stroke_width=1))
        dwg.add(
            dwg.text(
                m.strftime("%b %Y"),
                insert=(mx, axis_y),
                font_size="11px",
                font_family=FONT_FAMILY,
                fill=MUTED_INK,
                text_anchor="middle",
            )
        )

    # balance y-axis ticks (within trunk band)
    for tick in _y_ticks(min_level, max_level):
        ty = y_of(tick)
        if trunk_top - 2 <= ty <= trunk_bottom + 2:
            dwg.add(dwg.line(start=(x_left, ty), end=(x_right, ty), stroke=GRID_COLOR, stroke_width=1))
        dwg.add(
            dwg.text(
                _format_amount(tick, currency),
                insert=(margins.left - 12, ty + 4),
                font_size="11px",
                font_family=FONT_FAMILY,
                fill=MUTED_INK,
                text_anchor="end",
            )
        )

    dwg.add(
        dwg.line(
            start=(x_left, zero_y),
            end=(x_right, zero_y),
            stroke=BASELINE_COLOR,
            stroke_width=1.5,
            stroke_dasharray="4,3",
        )
    )

    # ribbons (drawn under the trunk so they appear to flow into/out of it)
    for p in placements:
        e = p.transition.entry
        if e.is_status:
            continue
        thickness = max(abs(e.amount) * (trunk_h / (max_level - min_level)), MIN_RIBBON_PX)
        row = p.row
        if e.amount >= 0:
            pill_bottom = trunk_top - STEM_GAP - row * (PILL_H + ROW_GAP_Y)
            pill_top = pill_bottom - PILL_H
            attach_top, attach_bot = p.attach_top, p.attach_bot
            link_d = _sankey_link_path(
                p.pill_x, pill_bottom - thickness / 2, pill_bottom + thickness / 2, p.x, attach_top, attach_bot
            )
        else:
            pill_top = trunk_bottom + STEM_GAP + row * (PILL_H + ROW_GAP_Y)
            pill_bottom = pill_top + PILL_H
            attach_top, attach_bot = p.attach_top, p.attach_bot
            link_d = _sankey_link_path(
                p.x, attach_top, attach_bot, p.pill_x, pill_top - thickness / 2, pill_top + thickness / 2
            )

        ribbon = dwg.path(d=link_d, fill=p.color, opacity=0.55, stroke="none")
        ribbon.set_desc(title=f"{e.date.isoformat()} — {e.asset}: {_format_amount(e.amount, currency, signed=True)}")
        dwg.add(ribbon)

    # trunk fill (positive/negative split) + stroke
    for seg, is_positive in fill_segments:
        if len(seg) < 2:
            continue
        fill = TRUNK_FILL if is_positive else TRUNK_NEGATIVE_FILL
        area_pts = seg + [(seg[-1][0], zero_y), (seg[0][0], zero_y)]
        dwg.add(dwg.polygon(points=area_pts, fill=fill, stroke="none", opacity=0.9))

    dwg.add(dwg.path(d=boundary_d, fill="none", stroke=TRUNK_STROKE, stroke_width=2.5, stroke_linejoin="round"))

    # pills
    for p in placements:
        e = p.transition.entry
        if e.is_status:
            continue
        label = f"{e.asset}: {_format_amount(e.amount, currency, signed=True)}"
        w = p.label_w
        row = p.row
        if e.amount >= 0:
            pill_bottom = trunk_top - STEM_GAP - row * (PILL_H + ROW_GAP_Y)
            pill_top = pill_bottom - PILL_H
        else:
            pill_top = trunk_bottom + STEM_GAP + row * (PILL_H + ROW_GAP_Y)
            pill_bottom = pill_top + PILL_H

        group = dwg.g()
        group.add(
            dwg.rect(
                insert=(p.pill_x - w / 2, pill_top),
                size=(w, PILL_H),
                rx=PILL_H / 2,
                ry=PILL_H / 2,
                fill=_blend(p.color, "#ffffff", 0.85),
                stroke=p.color,
                stroke_width=1.4,
            )
        )
        group.add(
            dwg.text(
                label,
                insert=(p.pill_x, pill_top + PILL_H / 2 + 4),
                font_size="11.5px",
                font_family=FONT_FAMILY,
                fill=PRIMARY_INK,
                text_anchor="middle",
            )
        )
        group.set_desc(title=f"{e.date.isoformat()} — {label}")
        dwg.add(group)

    # opening balance label
    status_entries = [t for t in transitions if t.entry.is_status]
    if status_entries:
        s = status_entries[0]
        dwg.add(
            dwg.text(
                f"Start: {_format_amount(s.level_after, currency)}  ({s.entry.date.isoformat()})",
                insert=(x_left, trunk_top - 10),
                font_size="12px",
                font_family=FONT_FAMILY,
                fill=SECONDARY_INK,
            )
        )
    dwg.add(
        dwg.text(
            f"End: {_format_amount(transitions[-1].level_after, currency)}  ({end_date.isoformat()})",
            insert=(x_right, trunk_top - 10),
            font_size="12px",
            font_family=FONT_FAMILY,
            fill=SECONDARY_INK,
            text_anchor="end",
        )
    )

    dwg.save()
    return out_path


def render_png(svg_path: str, png_path: str, scale: float = 2.0) -> str:
    try:
        import cairosvg
    except ImportError as exc:
        raise RuntimeError(
            "PNG export requires the optional 'cairosvg' package "
            "(pip install -r requirements-optional.txt)"
        ) from exc
    cairosvg.svg2png(url=svg_path, write_to=png_path, scale=scale)
    return png_path
