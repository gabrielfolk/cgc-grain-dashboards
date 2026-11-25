"""The "Inputs:" line on each page: every source the page uses, with how far its data runs and when it was
last updated, computed from the cached files at build time (never written by hand).

Each data script puts a list of these strings in its data.json as meta.inputs; the page shows them under
its as-of line. StatCan update dates come from StatCan's own table metadata (external.statcan_released):
a table's date is its latest update of any kind, which may be a release about other series in the same
table (32-10-0359 was updated 2026-10-09 for soybean and corn supply, not new production estimates), so
the canola page also names the production estimate itself.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

import external

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def day(d) -> str:
    """2026-11-15 -> Nov 15, 2026"""
    d = pd.Timestamp(d)
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def period(ref: str) -> str:
    """A StatCan REF_DATE as text: 2026 -> 2026, 2026-09 -> Sep 2026, 2026-11-14 -> Nov 14, 2026."""
    parts = str(ref).split("-")
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{MONTHS[int(parts[1]) - 1]} {parts[0]}"
    return day(ref)


def table_id(t: str) -> str:
    return f"{t[:2]}-{t[2:4]}-{t[4:]}"


def cgc(week: int, week_ending: str) -> str:
    return f"CGC Grain Statistics Weekly: week {week}, ending {day(week_ending)}"


def statcan(label: str, *tables: str) -> str:
    """'<label> (32-10-0077) to Sep 2026, updated Nov 10, 2026'. For several tables, the range of their
    latest periods (they run to different dates) and the most recent update."""
    ends = sorted(external.statcan_latest(t) for t in tables)
    released = [r for t in tables if (r := external.statcan_released(t))]
    ids = ", ".join(table_id(t) for t in tables) if len(tables) <= 2 else f"{len(tables)} tables"
    span = period(ends[-1]) if ends[0] == ends[-1] else f"{period(ends[0])} to {period(ends[-1])}"
    text = f"{label} ({ids}) to {span}" if len(tables) == 1 or ends[0] == ends[-1] else f"{label} ({ids}), latest data {span}"
    return text + (f", updated {day(max(released))}" if released else "")


def production(label: str = "StatCan production") -> str:
    """Production: the harvest year of the latest estimate in the table, and when the table was updated."""
    released = external.statcan_released("32100359")
    return (f"{label} (32-10-0359): {period(external.statcan_latest('32100359'))} crop, latest estimate"
            + (f" (table updated {day(released)})" if released else ""))


def canola_estimate(harvest_year: int) -> str:
    """The canola production estimate the forecast uses today, by name and release date."""
    t, name = external.production_known(harvest_year, pd.Timestamp(dt.date.today()))
    v = external.load()["vintages"]
    v = v[(v["harvest_year"] == harvest_year) & (v["release"] == name)]
    when = f", released {day(v['released'].max())}" if len(v) else ""
    return f"Forecast supply: StatCan {name} canola estimate, {t / 1e6:.2f} Mt{when}"


def aafc_slaughter() -> str:
    ends = [pd.to_datetime(external.open_csv(n)["EndDt_DtFin"]).max() for n in ("aafc_red_meat", "aafc_poultry")]
    return f"AAFC weekly slaughter to {day(min(ends))}"


def ers_cattle() -> str:
    e = external.open_csv("ers_cattle")
    last = e.loc[e["YEAR_ID"] == e["YEAR_ID"].max(), "TIMEPERIOD_ID"].max()
    return f"USDA ERS cattle trade to {MONTHS[int(last) - 1]} {int(e['YEAR_ID'].max())}"


def markets(label: str, *symbols: str) -> str:
    last = min(external.yahoo_weekly(s).index.max() for s in symbols)
    return f"{label} to week of {day(last)}"


def guide(zone: str, first: int, last: int) -> str:
    return f"Saskatchewan Crop Planning Guide, {zone} zone budgets, {first}–{last}"
