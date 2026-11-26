"""Build dashboard/methods/data.json: the figures the Methods page quotes, computed from CGC's data.

So far one item, CGC revisions. CGC applies revisions only to its crop-year totals, never to the weekly
("Current Week") rows, so the weekly rows add up short of the crop-year total. For each main flow this
measures that gap,

    gap = 1 − (sum of Current Week rows to week w) ÷ (crop-year total at week w)

for the current crop year to its latest week, for the five crop years before it at the same week, and for
those five years in full. It also lists crops whose gap this year is outside their own five-year range.
Run after src/ingest.py:
    python src/methods_data.py
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from db import connect

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dashboard" / "methods" / "data.json"

# CGC worksheet and metric for each flow, with the label the Methods page uses
SERIES = [
    ("deliveries", "Primary", "Deliveries", "Deliveries to country elevators"),
    ("direct", "Process", "Producer Deliveries", "Deliveries direct to processors"),
    ("shipments", "Primary", "Shipments", "Country elevator shipments"),
    ("process", "Process", "Milled/Mfg Grain", "Processing"),
    ("exports", "Terminal Exports", "Exports", "Terminal exports"),
]
YEARS_BACK = 5
MIN_KT = 100      # a crop's flow must be at least this big this year to be flagged
MIN_EXCESS = 0.02  # and its gap at least this far (2 points) outside its own five-year range


def gaps(con, worksheet: str, metric: str) -> dict:
    """{(crop_year, grain): {week: (crop-year total, sum of weekly rows to that week)}}, grain '*' = all crops."""
    rows = con.execute("""
        with t as (
            select crop_year, grain_week, grain::varchar as grain,
                   sum(ktonnes) filter (where period = 'Crop Year') as cy,
                   sum(ktonnes) filter (where period = 'Current Week') as cw
            from gsw where worksheet = ? and metric = ? and period in ('Crop Year', 'Current Week')
            group by all),
        g as (select * from t union all
              select crop_year, grain_week, '*', sum(cy), sum(cw) from t group by crop_year, grain_week)
        select crop_year, grain, grain_week, cy,
               sum(coalesce(cw, 0)) over (partition by crop_year, grain order by grain_week) as cw_sum
        from g order by 1, 2, 3""", [worksheet, metric]).fetchall()
    out: dict = {}
    for year, grain, week, cy, cw_sum in rows:
        if cy is not None:
            out.setdefault((year, grain), {})[week] = (cy, cw_sum)
    return out


def gap_at(weeks: dict, week: int | None = None) -> tuple[float, float] | None:
    """(gap, crop-year total) at `week` (or at the latest week up to it; the last week when None)."""
    if not weeks:
        return None
    usable = [w for w in weeks if week is None or w <= week]
    if not usable:
        return None
    cy, cw = weeks[max(usable)]
    return (1 - cw / cy, cy) if cy > 0 else None


def main() -> None:
    con = connect()
    years = [y for (y,) in con.execute("select distinct crop_year from gsw order by 1").fetchall()]
    cur, past = years[-1], years[-1 - YEARS_BACK:-1]
    week = con.execute("select max(grain_week) from gsw where crop_year = ?", [cur]).fetchone()[0]

    series, flags = [], []
    for key, ws, metric, label in SERIES:
        g = gaps(con, ws, metric)
        now = gap_at(g.get((cur, "*")), week)
        same = [gap_at(g.get((y, "*")), week) for y in past]
        full = [gap_at(g.get((y, "*"))) for y in past]
        series.append({
            "key": key, "label": label,
            "current": None if now is None else round(now[0], 4),
            "same_week": [None if x is None else round(x[0], 4) for x in same],
            "full_year": [None if x is None else round(x[0], 4) for x in full],
        })
        for grain in sorted({gr for (y, gr) in g if y == cur and gr != "*"}):
            now_g = gap_at(g.get((cur, grain)), week)
            hist = [x[0] for x in (gap_at(g.get((y, grain)), week) for y in past) if x is not None]
            if now_g is None or now_g[1] < MIN_KT or len(hist) < 3:
                continue
            lo, hi = min(hist), max(hist)
            excess = now_g[0] - hi if now_g[0] > hi else lo - now_g[0] if now_g[0] < lo else 0
            if excess >= MIN_EXCESS:
                flags.append({"grain": grain, "series": label, "current": round(now_g[0], 4),
                              "lo": round(lo, 4), "hi": round(hi, 4), "kt": round(now_g[1], 1),
                              "excess": round(excess, 4)})
    flags.sort(key=lambda f: -f["excess"])

    data = {
        "meta": {"current_year": cur, "latest_week": week, "past_years": past,
                 "generated": dt.date.today().isoformat()},
        "revisions": {"series": series, "flags": flags},
    }
    OUT.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False))
    print(f"Wrote {OUT.relative_to(ROOT)}: week {week}, {len(flags)} flagged")
    for f in flags:
        print(f"  {f['grain']} {f['series']}: {f['current']:.1%} vs {f['lo']:.1%}–{f['hi']:.1%}")


if __name__ == "__main__":
    main()
