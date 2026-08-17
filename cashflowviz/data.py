"""Parse the CashFlowViz input workbook and compute balance transitions."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import openpyxl

STATUS_LABEL = "status"


@dataclass(frozen=True)
class Entry:
    asset: str
    date: dt.date
    amount: float
    is_status: bool


@dataclass(frozen=True)
class Transition:
    """One entry's effect on the running balance, in chronological order."""

    entry: Entry
    level_before: float
    level_after: float


def _to_date(value) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    raise ValueError(f"Cell does not contain a date: {value!r}")


def read_entries(xlsx_path: str) -> list[Entry]:
    """Read (Assets, Date, IN/OUT) rows from the first sheet.

    The header row is located by name so column order/position doesn't matter.
    A row whose Assets value is "STATUS" (case-insensitive) sets the balance
    to an absolute value on that date instead of adding a delta.
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active

    header_row_idx = None
    col_of = {}
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        names = {str(c.value).strip().lower(): c.column for c in row if c.value is not None}
        if {"assets", "date", "in/out"} <= names.keys():
            header_row_idx = row[0].row
            col_of = names
            break
    if header_row_idx is None:
        raise ValueError(
            "Could not find header row with 'Assets', 'Date', 'IN/OUT' columns"
        )

    asset_col = col_of["assets"]
    date_col = col_of["date"]
    amount_col = col_of["in/out"]

    entries: list[Entry] = []
    for row in ws.iter_rows(min_row=header_row_idx + 1, max_row=ws.max_row):
        asset_cell = ws.cell(row=row[0].row, column=asset_col).value
        date_cell = ws.cell(row=row[0].row, column=date_col).value
        amount_cell = ws.cell(row=row[0].row, column=amount_col).value
        if asset_cell is None and date_cell is None and amount_cell is None:
            continue
        if date_cell is None or amount_cell is None:
            continue
        asset = str(asset_cell).strip() if asset_cell is not None else ""
        date = _to_date(date_cell)
        amount = float(amount_cell)
        is_status = asset.lower() == STATUS_LABEL
        entries.append(Entry(asset=asset, date=date, amount=amount, is_status=is_status))

    if not entries:
        raise ValueError("No data rows found in the workbook")

    return entries


def compute_transitions(entries: list[Entry]) -> list[Transition]:
    """Order entries chronologically and compute the balance before/after each.

    STATUS entries set the balance to an absolute value; every other entry
    adds its amount to the running balance. Same-day entries keep their
    original spreadsheet order (stable sort).
    """
    ordered = sorted(entries, key=lambda e: e.date)

    transitions: list[Transition] = []
    level = 0.0
    for entry in ordered:
        level_before = level
        level = entry.amount if entry.is_status else level + entry.amount
        transitions.append(Transition(entry=entry, level_before=level_before, level_after=level))

    return transitions
