"""Render a day-by-day balance series as a Finary-style budget graph in SVG."""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import svgwrite

from .data import DayBalance

POSITIVE_COLOR = "#16a34a"
NEGATIVE_COLOR = "#dc2626"
GRID_COLOR = "#e2e8f0"
AXIS_TEXT_COLOR = "#475569"
ZERO_LINE_COLOR = "#94a3b8"
LINE_COLOR_MIX = "#0f172a"
BACKGROUND_COLOR = "#ffffff"
MARKER_FILL = "#ffffff"

FONT_FAMILY = "Helvetica, Arial, sans-serif"


@dataclass
class Margins:
    top: int = 60
    right: int = 40
    bottom: int = 60
    left: int = 100


def _nice_step(rough_step: float) -> float:
    if rough_step <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(rough_step))
    residual = rough_step / magnitude
    if residual < 1.5:
        nice = 1
    elif residual < 3:
        nice = 2
    elif residual < 7:
        nice = 5
    else:
        nice = 10
    return nice * magnitude


def _y_ticks(min_val: float, max_val: float, target_count: int = 6) -> list[float]:
    if min_val == max_val:
        min_val -= 1
        max_val += 1
    span = max_val - min_val
    step = _nice_step(span / target_count)
    start = math.floor(min_val / step) * step
    ticks = []
    v = start
    while v <= max_val + step * 0.001:
        ticks.append(round(v, 6))
        v += step
    return ticks


def _format_amount(value: float, currency: str) -> str:
    text = f"{value:,.0f}".replace(",", " ")
    if value > 0:
        text = f"+{text}"
    if currency:
        text = f"{text} {currency}"
    return text


def _month_ticks(start: dt.date, end: dt.date) -> list[dt.date]:
    ticks = []
    cur = start.replace(day=1)
    if cur < start:
        pass
    while cur <= end:
        ticks.append(cur)
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    if not ticks or ticks[0] != start:
        ticks.insert(0, start)
    return ticks


def render_svg(
    days: list[DayBalance],
    out_path: str,
    width: int = 1200,
    height: int = 640,
    title: str = "Cash Flow Balance",
    currency: str = "",
) -> str:
    if not days:
        raise ValueError("No daily balance data to render")

    margins = Margins()
    plot_w = width - margins.left - margins.right
    plot_h = height - margins.top - margins.bottom

    start_date = days[0].date
    end_date = days[-1].date
    date_span = max((end_date - start_date).days, 1)

    balances = [d.balance for d in days]
    min_bal = min(balances + [0.0])
    max_bal = max(balances + [0.0])
    pad = (max_bal - min_bal) * 0.1 or max(abs(max_bal), 1.0) * 0.1
    min_bal -= pad
    max_bal += pad

    def x(date: dt.date) -> float:
        return margins.left + (date - start_date).days / date_span * plot_w

    def y(value: float) -> float:
        return margins.top + (max_bal - value) / (max_bal - min_bal) * plot_h

    dwg = svgwrite.Drawing(out_path, size=(width, height), profile="full")
    dwg.add(dwg.rect(insert=(0, 0), size=(width, height), fill=BACKGROUND_COLOR))

    pos_grad = dwg.defs.add(
        dwg.linearGradient(start=(0, 0), end=(0, 1), id="posGradient")
    )
    pos_grad.add_stop_color(0, color=POSITIVE_COLOR, opacity=0.35)
    pos_grad.add_stop_color(1, color=POSITIVE_COLOR, opacity=0.02)

    neg_grad = dwg.defs.add(
        dwg.linearGradient(start=(0, 0), end=(0, 1), id="negGradient")
    )
    neg_grad.add_stop_color(0, color=NEGATIVE_COLOR, opacity=0.02)
    neg_grad.add_stop_color(1, color=NEGATIVE_COLOR, opacity=0.35)

    dwg.add(
        dwg.text(
            title,
            insert=(margins.left, 32),
            font_size="22px",
            font_family=FONT_FAMILY,
            font_weight="bold",
            fill="#0f172a",
        )
    )

    for tick in _y_ticks(min_bal, max_bal):
        ty = y(tick)
        dwg.add(
            dwg.line(
                start=(margins.left, ty),
                end=(width - margins.right, ty),
                stroke=GRID_COLOR,
                stroke_width=1,
            )
        )
        dwg.add(
            dwg.text(
                _format_amount(tick, currency),
                insert=(margins.left - 12, ty + 4),
                font_size="12px",
                font_family=FONT_FAMILY,
                fill=AXIS_TEXT_COLOR,
                text_anchor="end",
            )
        )

    for m in _month_ticks(start_date, end_date):
        mx = x(m)
        dwg.add(
            dwg.line(
                start=(mx, margins.top),
                end=(mx, height - margins.bottom),
                stroke=GRID_COLOR,
                stroke_width=1,
            )
        )
        dwg.add(
            dwg.text(
                m.strftime("%b %Y"),
                insert=(mx, height - margins.bottom + 20),
                font_size="12px",
                font_family=FONT_FAMILY,
                fill=AXIS_TEXT_COLOR,
                text_anchor="middle",
            )
        )

    zero_y = y(0)
    dwg.add(
        dwg.line(
            start=(margins.left, zero_y),
            end=(width - margins.right, zero_y),
            stroke=ZERO_LINE_COLOR,
            stroke_width=1.5,
            stroke_dasharray="4,3",
        )
    )

    points = [(x(d.date), y(d.balance), d.balance) for d in days]

    segments: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = [points[0][:2]]
    for (px0, py0, b0), (px1, py1, b1) in zip(points, points[1:]):
        if (b0 >= 0) == (b1 >= 0) or b0 == 0 or b1 == 0:
            current.append((px1, py1))
        else:
            t = b0 / (b0 - b1)
            cross_x = px0 + t * (px1 - px0)
            current.append((cross_x, zero_y))
            segments.append(current)
            current = [(cross_x, zero_y), (px1, py1)]
    segments.append(current)

    for seg in segments:
        if len(seg) < 2:
            continue
        avg_b = sum(zero_y - p[1] for p in seg)
        is_positive = avg_b >= 0
        color = POSITIVE_COLOR if is_positive else NEGATIVE_COLOR
        fill = "url(#posGradient)" if is_positive else "url(#negGradient)"

        area_points = seg + [(seg[-1][0], zero_y), (seg[0][0], zero_y)]
        dwg.add(dwg.polygon(points=area_points, fill=fill, stroke="none"))

        dwg.add(
            dwg.polyline(
                points=seg,
                fill="none",
                stroke=color,
                stroke_width=2.5,
                stroke_linejoin="round",
                stroke_linecap="round",
            )
        )

    for d in days:
        if not d.events:
            continue
        for e in d.events:
            cx, cy = x(d.date), y(d.balance)
            color = POSITIVE_COLOR if e.amount >= 0 else NEGATIVE_COLOR
            if e.is_status:
                color = LINE_COLOR_MIX
            circle = dwg.circle(
                center=(cx, cy),
                r=4,
                fill=MARKER_FILL,
                stroke=color,
                stroke_width=2,
            )
            label = e.asset if e.is_status else f"{e.asset}: {_format_amount(e.amount, currency)}"
            circle.set_desc(title=f"{d.date.isoformat()} — {label}")
            dwg.add(circle)

    dwg.add(
        dwg.text(
            f"Start: {_format_amount(days[0].balance, currency)}  ({start_date.isoformat()})",
            insert=(margins.left, margins.top - 12),
            font_size="12px",
            font_family=FONT_FAMILY,
            fill=AXIS_TEXT_COLOR,
        )
    )
    dwg.add(
        dwg.text(
            f"End: {_format_amount(days[-1].balance, currency)}  ({end_date.isoformat()})",
            insert=(width - margins.right, margins.top - 12),
            font_size="12px",
            font_family=FONT_FAMILY,
            fill=AXIS_TEXT_COLOR,
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
