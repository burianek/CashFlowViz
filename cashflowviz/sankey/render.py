"""Layout and SVG rendering for the multi-column Sankey cash flow diagram.

Two layout modes:

- categorical (default): columns are assigned deterministically rather than
  via a generic Sankey layout algorithm, since the graph shape is known up
  front — income sources for month m sit at column 2m-1, that month's hub at
  column 2m, and its expenses (or the terminal balance, for the last month)
  at column 2m+1.
- date-positioned (``date_positioned=True``): the x-axis is a real
  proportional date scale instead. Source/sink nodes sit at their own entry's
  date; each hub sits at the last calendar day of its month (always after its
  own month's entries, always before the next month's); the terminal node
  (no date of its own) sits a fixed offset after the last hub.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

import svgwrite

from .data import Link, Node, SankeyGraph

GOOD_COLOR = "#0ca30c"
CRITICAL_COLOR = "#d03b3b"
HUB_COLOR = "#8b8f99"
HUB_STROKE = "#5b5f68"
SURFACE_COLOR = "#fcfcfb"
PRIMARY_INK = "#0b0b0b"
SECONDARY_INK = "#52514e"
MUTED_INK = "#898781"
GRID_COLOR = "#e1e0d9"

FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif'

NODE_W = 20
COLUMN_SPACING = 250
NODE_GAP_Y = 14
MIN_NODE_H = 4.0
MIN_LABELED_NODE_H = 28.0  # source/sink bars need room for a 2-line label beside them
LABEL_GAP = 8
DATE_AXIS_WIDTH = 1400
MAX_DATE_AXIS_WIDTH = 6000  # hard cap: beyond this, very dense clusters may compress a bit
MIN_DATE_GAP_PX = 160  # min px between the two closest distinct dates, for label clearance
TERMINAL_OFFSET = 150  # px placed after the last hub, in date-positioned mode


def _min_node_h(node: Node) -> float:
    return MIN_LABELED_NODE_H if node.kind in ("source", "sink", "terminal") else MIN_NODE_H


@dataclass
class _Margins:
    top: int = 96
    right: int = 190
    bottom: int = 40
    left: int = 190


def _format_amount(value: float, currency: str) -> str:
    text = f"{abs(value):,.0f}".replace(",", " ")
    if currency:
        text = f"{text} {currency}"
    return text


def _node_fill(node: Node) -> str:
    if node.kind == "source":
        return GOOD_COLOR
    if node.kind == "sink":
        return CRITICAL_COLOR
    return CRITICAL_COLOR if node.negative else HUB_COLOR


def _node_stroke(node: Node) -> str:
    if node.kind == "source":
        return "#087a08"
    if node.kind == "sink":
        return "#a52c2c"
    return "#a52c2c" if node.negative else HUB_STROKE


def _sankey_link_path(x0, y0_top, y0_bot, x1, y1_top, y1_bot) -> str:
    xm = (x0 + x1) / 2
    return (
        f"M {x0:.2f},{y0_top:.2f} "
        f"C {xm:.2f},{y0_top:.2f} {xm:.2f},{y1_top:.2f} {x1:.2f},{y1_top:.2f} "
        f"L {x1:.2f},{y1_bot:.2f} "
        f"C {xm:.2f},{y1_bot:.2f} {xm:.2f},{y0_bot:.2f} {x0:.2f},{y0_bot:.2f} Z"
    )


def _month_starts(min_date: dt.date, max_date: dt.date) -> list[dt.date]:
    ticks = []
    cur = min_date.replace(day=1)
    while cur <= max_date:
        ticks.append(cur)
        cur = cur.replace(year=cur.year + 1, month=1) if cur.month == 12 else cur.replace(month=cur.month + 1)
    return ticks


def render_svg(
    graph: SankeyGraph,
    out_path: str,
    width: int | None = None,
    height: int = 780,
    title: str = "Cash Flow",
    currency: str = "",
    date_positioned: bool = False,
) -> str:
    if not graph.nodes:
        raise ValueError("No nodes to render")

    margins = _Margins()
    if date_positioned:
        margins.bottom = max(margins.bottom, 60)
        # the terminal node (no date of its own) sits TERMINAL_OFFSET past the
        # last hub, then needs its own label's width again past that
        margins.right = max(margins.right, TERMINAL_OFFSET + 170)
    nodes_by_id = {n.id: n for n in graph.nodes}

    # --- horizontal placement: either fixed categorical columns, or a real date scale ---
    node_x: dict[str, float] = {}
    stack_key: dict[str, object] = {}
    month_tick_x: list[tuple[float, str]] = []

    if date_positioned:
        dated_nodes = [n for n in graph.nodes if n.date is not None]
        min_date = min(n.date for n in dated_nodes)
        max_date = max(n.date for n in dated_nodes)
        span_days = max((max_date - min_date).days, 1)

        # Guarantee the closest two distinct dates still get enough width apart
        # for their labels — a plain proportional scale would let a tight
        # cluster of same-week transactions collide into unreadable overlap.
        unique_dates = sorted({n.date for n in dated_nodes})
        min_gap_days = min(
            ((b - a).days for a, b in zip(unique_dates, unique_dates[1:])), default=span_days
        )
        px_per_day = MIN_DATE_GAP_PX / max(min_gap_days, 1)
        needed_plot_w = min(px_per_day * span_days, MAX_DATE_AXIS_WIDTH - margins.left - margins.right)
        plot_w = max(DATE_AXIS_WIDTH - margins.left - margins.right, needed_plot_w, (width or 0) - margins.left - margins.right)
        width = margins.left + margins.right + plot_w
        plot_left, plot_right = margins.left, width - margins.right

        def x_of(d: dt.date) -> float:
            return plot_left + (d - min_date).days / span_days * (plot_right - plot_left)

        for n in graph.nodes:
            if n.date is not None:
                node_x[n.id] = x_of(n.date)
                stack_key[n.id] = n.date

        last_hub_x = max((node_x[n.id] for n in graph.nodes if n.kind == "hub"), default=plot_left)
        for n in graph.nodes:
            if n.id not in node_x:  # terminal node: no date of its own
                node_x[n.id] = last_hub_x + TERMINAL_OFFSET
                stack_key[n.id] = ("terminal", n.id)

        month_tick_x = [(x_of(d), f"{d.month:02d}/{d.year}") for d in _month_starts(min_date, max_date)]
    else:
        columns = sorted({n.column for n in graph.nodes})
        col_x = {col: margins.left + i * COLUMN_SPACING for i, col in enumerate(columns)}
        width = max(width or 0, margins.left + (len(columns) - 1) * COLUMN_SPACING + NODE_W + margins.right)
        for n in graph.nodes:
            node_x[n.id] = col_x[n.column]
            stack_key[n.id] = n.column

    # --- vertical scale: find k (px per currency unit) so the densest stack fits ---
    nodes_by_group: dict[object, list[Node]] = defaultdict(list)
    for n in graph.nodes:
        nodes_by_group[stack_key[n.id]].append(n)
    for group_nodes in nodes_by_group.values():
        group_nodes.sort(key=lambda n: (n.date or dt.date.min, n.kind, n.label))

    plot_top = margins.top
    plot_h = height - margins.top - margins.bottom
    k = plot_h
    for group_nodes in nodes_by_group.values():
        total_value = sum(n.value for n in group_nodes)
        if total_value <= 0:
            continue
        gap_budget = (len(group_nodes) - 1) * NODE_GAP_Y
        bound = max(plot_h - gap_budget, 10.0) / total_value
        k = min(k, bound)

    # --- stack nodes top-down within each group ---
    y_top: dict[str, float] = {}
    y_bottom: dict[str, float] = {}
    for group_nodes in nodes_by_group.values():
        cursor = plot_top
        for n in group_nodes:
            h = max(n.value * k, _min_node_h(n))
            y_top[n.id] = cursor
            y_bottom[n.id] = cursor + h
            cursor += h + NODE_GAP_Y

    content_bottom = max(y_bottom.values())
    total_height = max(height, content_bottom + margins.bottom)

    # --- allocate link ports on each node's in/out edges, ordered to reduce crossings ---
    out_links: dict[str, list[Link]] = defaultdict(list)
    in_links: dict[str, list[Link]] = defaultdict(list)
    for l in graph.links:
        out_links[l.source].append(l)
        in_links[l.target].append(l)

    def _center(node_id: str) -> float:
        return (y_top[node_id] + y_bottom[node_id]) / 2

    source_port: dict[int, tuple[float, float]] = {}
    target_port: dict[int, tuple[float, float]] = {}

    for node_id, links in out_links.items():
        links.sort(key=lambda l: _center(l.target))
        total = sum(l.value for l in links) or 1.0
        span = y_bottom[node_id] - y_top[node_id]
        cursor = y_top[node_id]
        for l in links:
            h = span * (l.value / total)
            source_port[id(l)] = (cursor, cursor + h)
            cursor += h

    for node_id, links in in_links.items():
        links.sort(key=lambda l: _center(l.source))
        total = sum(l.value for l in links) or 1.0
        span = y_bottom[node_id] - y_top[node_id]
        cursor = y_top[node_id]
        for l in links:
            h = span * (l.value / total)
            target_port[id(l)] = (cursor, cursor + h)
            cursor += h

    # ======================================================================
    dwg = svgwrite.Drawing(out_path, size=(width, total_height), profile="full")
    dwg.add(dwg.rect(insert=(0, 0), size=(width, total_height), fill=SURFACE_COLOR))

    if date_positioned:
        for mx, mlabel in month_tick_x:
            dwg.add(
                dwg.line(start=(mx, plot_top - 10), end=(mx, content_bottom), stroke=GRID_COLOR, stroke_width=1)
            )
            dwg.add(
                dwg.text(
                    mlabel,
                    insert=(mx, content_bottom + 20),
                    font_size="11px",
                    font_family=FONT_FAMILY,
                    fill=MUTED_INK,
                    text_anchor="middle",
                )
            )

    dwg.add(
        dwg.text(
            title,
            insert=(margins.left, 34),
            font_size="20px",
            font_family=FONT_FAMILY,
            font_weight="600",
            fill=PRIMARY_INK,
        )
    )
    legend_items = [("Income", GOOD_COLOR), ("Expense", CRITICAL_COLOR), ("Month milestone", HUB_COLOR)]
    lx = margins.left
    for label, color in legend_items:
        dwg.add(dwg.rect(insert=(lx, 52), size=(11, 11), rx=2, ry=2, fill=color))
        dwg.add(
            dwg.text(label, insert=(lx + 16, 61), font_size="11px", font_family=FONT_FAMILY, fill=SECONDARY_INK)
        )
        lx += 16 + len(label) * 6.2 + 22

    # links (under nodes), each a gradient from its source node's color to its target's
    for idx, l in enumerate(graph.links):
        src, tgt = nodes_by_id[l.source], nodes_by_id[l.target]
        x0 = node_x[src.id] + NODE_W
        x1 = node_x[tgt.id]
        sy0, sy1 = source_port[id(l)]
        ty0, ty1 = target_port[id(l)]
        grad_id = f"linkgrad{idx}"
        grad = dwg.defs.add(dwg.linearGradient(start=(0, 0), end=(1, 0), id=grad_id))
        grad.add_stop_color(0, color=_node_fill(src), opacity=0.55)
        grad.add_stop_color(1, color=_node_fill(tgt), opacity=0.55)
        path_d = _sankey_link_path(x0, sy0, sy1, x1, ty0, ty1)
        link = dwg.path(d=path_d, fill=f"url(#{grad_id})", stroke="none")
        link.set_desc(title=f"{src.label} -> {tgt.label}: {_format_amount(l.value, currency)}")
        dwg.add(link)

    # nodes
    for n in graph.nodes:
        x = node_x[n.id]
        yt, yb = y_top[n.id], y_bottom[n.id]
        group = dwg.g()
        group.add(
            dwg.rect(
                insert=(x, yt),
                size=(NODE_W, max(yb - yt, 1)),
                rx=2,
                ry=2,
                fill=_node_fill(n),
                stroke=_node_stroke(n),
                stroke_width=1,
            )
        )
        cy = (yt + yb) / 2
        amount_text = _format_amount(n.value, currency)

        if n.kind == "source":
            group.add(
                dwg.text(
                    n.label,
                    insert=(x - LABEL_GAP, cy - 3),
                    font_size="12px",
                    font_family=FONT_FAMILY,
                    font_weight="600",
                    fill=PRIMARY_INK,
                    text_anchor="end",
                )
            )
            group.add(
                dwg.text(
                    amount_text,
                    insert=(x - LABEL_GAP, cy + 11),
                    font_size="11px",
                    font_family=FONT_FAMILY,
                    fill=SECONDARY_INK,
                    text_anchor="end",
                )
            )
        elif n.kind in ("sink", "terminal"):
            lx2 = x + NODE_W + LABEL_GAP
            group.add(
                dwg.text(
                    n.label,
                    insert=(lx2, cy - 3),
                    font_size="12px",
                    font_family=FONT_FAMILY,
                    font_weight="600",
                    fill=PRIMARY_INK,
                )
            )
            group.add(
                dwg.text(
                    amount_text,
                    insert=(lx2, cy + 11),
                    font_size="11px",
                    font_family=FONT_FAMILY,
                    fill=SECONDARY_INK,
                )
            )
        else:  # hub
            group.add(
                dwg.text(
                    n.label,
                    insert=(x + NODE_W / 2, yb + 16),
                    font_size="12px",
                    font_family=FONT_FAMILY,
                    font_weight="600",
                    fill=PRIMARY_INK,
                    text_anchor="middle",
                )
            )
            group.add(
                dwg.text(
                    amount_text,
                    insert=(x + NODE_W / 2, yb + 30),
                    font_size="11px",
                    font_family=FONT_FAMILY,
                    fill=SECONDARY_INK,
                    text_anchor="middle",
                )
            )

        group.set_desc(title=f"{n.label}: {amount_text}")
        dwg.add(group)

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
