"""Parse the FROM/AMOUNT/DATE cashflow format and build a month-hub Sankey graph.

Each row is either income (positive AMOUNT) or an expense (negative AMOUNT),
dated on the day it hits the balance. Rows are grouped by the calendar month
of their DATE into a chronological chain of month "hub" nodes: each hub
receives the balance carried over from the previous month plus that month's
income, and sends out that month's expenses plus whatever balance carries
forward to the next month.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass

from ..io import FlowEntry, read_flow_entries

__all__ = [
    "FlowEntry",
    "read_flow_entries",
    "Node",
    "Link",
    "SankeyGraph",
    "build_graph",
    "month_label",
    "month_end",
]

CZECH_MONTHS = {
    # Uppercase, diacritics stripped — matches the "SRPEN" / "ZARI" convention
    # from the reference template rather than full Czech orthography.
    1: "LEDEN",
    2: "UNOR",
    3: "BREZEN",
    4: "DUBEN",
    5: "KVETEN",
    6: "CERVEN",
    7: "CERVENEC",
    8: "SRPEN",
    9: "ZARI",
    10: "RIJEN",
    11: "LISTOPAD",
    12: "PROSINEC",
}


def month_label(year: int, month: int) -> str:
    return f"{CZECH_MONTHS[month]} {year}"


def month_end(year: int, month: int) -> dt.date:
    """Last calendar day of the month — always on/after every entry dated in
    it and strictly before the next month's entries, so it's a safe anchor
    point for a month-milestone node on a real date axis."""
    return dt.date(year, month, calendar.monthrange(year, month)[1])


@dataclass
class Node:
    id: str
    label: str
    kind: str  # 'source' | 'sink' | 'hub' | 'terminal'
    column: int
    value: float = 0.0
    date: dt.date | None = None
    negative: bool = False  # hub/terminal only: True if it's running a deficit


@dataclass
class Link:
    source: str
    target: str
    value: float
    kind: str  # 'income' | 'expense' | 'carry_pos' | 'carry_neg'


@dataclass
class SankeyGraph:
    nodes: list[Node]
    links: list[Link]


def build_graph(entries: list[FlowEntry]) -> SankeyGraph:
    months = sorted({(e.date.year, e.date.month) for e in entries})
    month_index = {ym: i for i, ym in enumerate(months)}

    nodes: dict[str, Node] = {}
    links: list[Link] = []

    name_counts: dict[str, int] = {}

    def unique_name(base: str) -> str:
        """First use of a FROM name is shown as-is; every repeat gets a
        running number appended (HALA, HALA 2, HALA 3, ...) so the name is
        always unique, however many rows — same date or different — share it."""
        count = name_counts.get(base, 0) + 1
        name_counts[base] = count
        return base if count == 1 else f"{base} {count}"

    hub_id_of: dict[tuple[int, int], str] = {}
    for ym in months:
        hub_id = f"HUB:{ym[0]}-{ym[1]:02d}"
        hub_id_of[ym] = hub_id
        nodes[hub_id] = Node(
            id=hub_id, label=month_label(*ym), kind="hub", column=2 * month_index[ym], date=month_end(*ym)
        )

    for e in entries:
        if e.amount == 0:
            continue
        ym = (e.date.year, e.date.month)
        m_idx = month_index[ym]
        hub_id = hub_id_of[ym]
        node_id = unique_name(e.name)
        if e.amount > 0:
            nodes[node_id] = Node(
                id=node_id, label=node_id, kind="source", column=2 * m_idx - 1, value=e.amount, date=e.date
            )
            links.append(Link(source=node_id, target=hub_id, value=e.amount, kind="income"))
        else:
            amt = abs(e.amount)
            nodes[node_id] = Node(
                id=node_id, label=node_id, kind="sink", column=2 * m_idx + 1, value=amt, date=e.date
            )
            links.append(Link(source=hub_id, target=node_id, value=amt, kind="expense"))

    running = 0.0
    for i, ym in enumerate(months):
        hub_id = hub_id_of[ym]
        income_total = sum(l.value for l in links if l.target == hub_id and l.kind == "income")
        expense_total = sum(l.value for l in links if l.source == hub_id and l.kind == "expense")
        total_in = running + income_total
        carry_out = total_in - expense_total
        nodes[hub_id].value = max(total_in, expense_total, abs(carry_out), 1e-9)
        nodes[hub_id].negative = carry_out < 0

        carry_kind = "carry_pos" if carry_out >= 0 else "carry_neg"
        if i + 1 < len(months):
            next_hub = hub_id_of[months[i + 1]]
            links.append(Link(source=hub_id, target=next_hub, value=abs(carry_out), kind=carry_kind))
        else:
            term_id = "TERMINAL"
            nodes[term_id] = Node(
                id=term_id,
                label=f"Final balance ({month_label(*ym)})",
                kind="terminal",
                column=nodes[hub_id].column + 1,
                value=abs(carry_out),
                negative=carry_out < 0,
            )
            links.append(Link(source=hub_id, target=term_id, value=abs(carry_out), kind=carry_kind))
        running = carry_out

    return SankeyGraph(nodes=list(nodes.values()), links=links)
