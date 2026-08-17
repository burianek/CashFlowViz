"""Layout and SVG rendering for the multi-column Sankey cash flow diagram.

Columns are assigned deterministically rather than via a generic Sankey
layout algorithm, since the graph shape is known up front: income sources
for month m sit at column 2m-1, that month's hub at column 2m, and its
expenses (or the terminal balance, for the last month) at column 2m+1.
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

FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif'

NODE_W = 20
COLUMN_SPACING = 250
NODE_GAP_Y = 14
MIN_NODE_H = 4.0
MIN_LABELED_NODE_H = 28.0  # source/sink bars need room for a 2-line label beside them
LABEL_GAP = 8


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


def render_svg(
    graph: SankeyGraph,
    out_path: str,
    width: int | None = None,
    height: int = 780,
    title: str = "Cash Flow",
    currency: str = "",
) -> str:
    if not graph.nodes:
        raise ValueError("No nodes to render")

    margins = _Margins()
    nodes_by_id = {n.id: n for n in graph.nodes}

    columns = sorted({n.column for n in graph.nodes})
    col_x = {col: margins.left + i * COLUMN_SPACING for i, col in enumerate(columns)}
    computed_width = margins.left + (len(columns) - 1) * COLUMN_SPACING + NODE_W + margins.right
    width = max(width or 0, computed_width)

    # --- vertical scale: find k (px per currency unit) so the densest column fits ---
    nodes_by_col: dict[int, list[Node]] = defaultdict(list)
    for n in graph.nodes:
        nodes_by_col[n.column].append(n)
    for col_nodes in nodes_by_col.values():
        col_nodes.sort(key=lambda n: (n.date or dt.date.min, n.kind, n.label))

    plot_top = margins.top
    plot_h = height - margins.top - margins.bottom
    k = plot_h
    for col_nodes in nodes_by_col.values():
        total_value = sum(n.value for n in col_nodes)
        if total_value <= 0:
            continue
        gap_budget = (len(col_nodes) - 1) * NODE_GAP_Y
        bound = max(plot_h - gap_budget, 10.0) / total_value
        k = min(k, bound)

    # --- stack nodes top-down within each column ---
    y_top: dict[str, float] = {}
    y_bottom: dict[str, float] = {}
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
        x0 = col_x[src.column] + NODE_W
        x1 = col_x[tgt.column]
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
        x = col_x[n.column]
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
