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
LANE_GAP_X = 24  # horizontal buffer required between two nodes' footprints before they can share a lane
TARGET_MAX_BAR_H = 260.0  # date mode: pixel height of the single largest-value node
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


def _estimate_text_width(text: str, font_px: float = 12.0) -> float:
    return len(text) * font_px * 0.6


def _assign_lanes(footprints: list[tuple[str, float, float, int | None]]) -> dict[str, int]:
    """Greedy interval scheduling: pack (node_id, left, right, forced_lane)
    footprints into the fewest lanes such that nothing in the same lane
    overlaps. Items with a non-None forced_lane (month hubs, pinned to lane 0
    so they stay aligned with the plot's top edge) are placed first, in their
    own x order, so free items placed afterward correctly route around them."""
    lane_right: list[float] = []
    lane_of: dict[str, int] = {}

    forced = sorted((f for f in footprints if f[3] is not None), key=lambda f: f[1])
    free = sorted((f for f in footprints if f[3] is None), key=lambda f: f[1])

    for node_id, _left, right, lane in forced:
        while len(lane_right) <= lane:
            lane_right.append(float("-inf"))
        lane_right[lane] = max(lane_right[lane], right)
        lane_of[node_id] = lane

    for node_id, left, right, _lane in free:
        placed = False
        for i, r in enumerate(lane_right):
            if left >= r + LANE_GAP_X:
                lane_right[i] = right
                lane_of[node_id] = i
                placed = True
                break
        if not placed:
            lane_right.append(right)
            lane_of[node_id] = len(lane_right) - 1
    return lane_of


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

    node_x: dict[str, float] = {}
    y_top: dict[str, float] = {}
    y_bottom: dict[str, float] = {}
    month_tick_x: list[tuple[float, str]] = []
    plot_top = margins.top

    if date_positioned:
        # --- horizontal: a real proportional date scale ---
        dated_nodes = [n for n in graph.nodes if n.date is not None]
        min_date = min(n.date for n in dated_nodes)
        max_date = max(n.date for n in dated_nodes)
        span_days = max((max_date - min_date).days, 1)
        width = max(width or 0, DATE_AXIS_WIDTH)
        plot_left, plot_right = margins.left, width - margins.right

        def x_of(d: dt.date) -> float:
            return plot_left + (d - min_date).days / span_days * (plot_right - plot_left)

        for n in graph.nodes:
            if n.date is not None:
                node_x[n.id] = x_of(n.date)

        last_hub_x = max((node_x[n.id] for n in graph.nodes if n.kind == "hub"), default=plot_left)
        for n in graph.nodes:
            if n.id not in node_x:  # terminal node: no date of its own
                node_x[n.id] = last_hub_x + TERMINAL_OFFSET

        month_tick_x = [(x_of(d), f"{d.month:02d}/{d.year}") for d in _month_starts(min_date, max_date)]

        # --- vertical: each node's height reflects only its own value ---
        target_h = min(TARGET_MAX_BAR_H, max((height - margins.top - margins.bottom) * 0.9, 40.0))
        max_value = max((n.value for n in graph.nodes), default=1.0) or 1.0
        k = target_h / max_value
        node_h = {n.id: max(n.value * k, _min_node_h(n)) for n in graph.nodes}

        # --- pack every node into as few vertical lanes as needed so no two
        # labels/bars ever overlap horizontally, instead of stretching the
        # whole canvas to fit the tightest date gap. Month hubs are pinned to
        # lane 0 (so they stay aligned with the plot's top edge) and placed
        # first, so source/sink/terminal nodes route around them too. ---
        def _label_w(n: Node) -> float:
            return max(_estimate_text_width(n.label, 12), _estimate_text_width(_format_amount(n.value, currency), 11))

        footprints: list[tuple[str, float, float, int | None]] = []
        for n in graph.nodes:
            x, w = node_x[n.id], _label_w(n)
            if n.kind == "source":
                footprints.append((n.id, x - LABEL_GAP - w, x + NODE_W, None))
            elif n.kind == "hub":
                footprints.append((n.id, x + NODE_W / 2 - w / 2, x + NODE_W / 2 + w / 2, 0))
            else:  # sink, terminal
                footprints.append((n.id, x, x + NODE_W + LABEL_GAP + w, None))

        lane_of = _assign_lanes(footprints)
        lane_count = max(lane_of.values(), default=-1) + 1
        lane_tallest = [0.0] * lane_count
        for n in graph.nodes:
            lane_tallest[lane_of[n.id]] = max(lane_tallest[lane_of[n.id]], node_h[n.id])

        lane_y_start = []
        cursor = plot_top
        for tallest in lane_tallest:
            lane_y_start.append(cursor)
            cursor += tallest + NODE_GAP_Y

        for n in graph.nodes:
            yt = lane_y_start[lane_of[n.id]]
            y_top[n.id] = yt
            y_bottom[n.id] = yt + node_h[n.id]

        content_bottom = max(y_bottom.values())
        total_height = max(height, content_bottom + margins.bottom)
    else:
        # --- fixed categorical columns ---
        columns = sorted({n.column for n in graph.nodes})
        col_x = {col: margins.left + i * COLUMN_SPACING for i, col in enumerate(columns)}
        width = max(width or 0, margins.left + (len(columns) - 1) * COLUMN_SPACING + NODE_W + margins.right)
        for n in graph.nodes:
            node_x[n.id] = col_x[n.column]

        nodes_by_col: dict[int, list[Node]] = defaultdict(list)
        for n in graph.nodes:
            nodes_by_col[n.column].append(n)
        for col_nodes in nodes_by_col.values():
            col_nodes.sort(key=lambda n: (n.date or dt.date.min, n.kind, n.label))

        plot_h = height - margins.top - margins.bottom
        k = plot_h
        for col_nodes in nodes_by_col.values():
            total_value = sum(n.value for n in col_nodes)
            if total_value <= 0:
                continue
            gap_budget = (len(col_nodes) - 1) * NODE_GAP_Y
            bound = max(plot_h - gap_budget, 10.0) / total_value
            k = min(k, bound)

        for col_nodes in nodes_by_col.values():
            cursor = plot_top
            for n in col_nodes:
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
