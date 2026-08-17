"""Compute running-balance transitions for the CashFlowViz budget-graph diagram."""

from __future__ import annotations

from dataclasses import dataclass

from .io import FlowEntry, read_flow_entries

__all__ = ["FlowEntry", "read_flow_entries", "Transition", "compute_transitions"]


@dataclass(frozen=True)
class Transition:
    """One entry's effect on the running balance, in chronological order."""

    entry: FlowEntry
    level_before: float
    level_after: float


def compute_transitions(entries: list[FlowEntry]) -> list[Transition]:
    """Order entries chronologically and compute the running balance before/after each.

    The balance starts at 0 and every entry adds its (positive or negative)
    amount to it. Same-day entries keep their original spreadsheet order
    (stable sort).
    """
    ordered = sorted(entries, key=lambda e: e.date)

    transitions: list[Transition] = []
    level = 0.0
    for entry in ordered:
        level_before = level
        level += entry.amount
        transitions.append(Transition(entry=entry, level_before=level_before, level_after=level))

    return transitions
