"""Build dashboard/data.json from the processed CGC data.

Run after src/ingest.py:
    python src/dashboard_data.py
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import external
from grain_data import flows_sql
from db import connect

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "dashboard" / "data.json"

WEEKS = 52
ALL = "All grains"
MIN_YEARS = 10  # skip grains CGC only reported briefly (buckwheat, millet...)
# CGC grain name -> StatCan field crop name (table 32-10-0359)
STATCAN_CROPS = {
    "Wheat": "Wheat, all excluding durum wheat",
    "Amber Durum": "Wheat, durum",
    "Canola": "Canola (rapeseed)",
    "Barley": "Barley",
    "Oats": "Oats",
    "Peas": "Peas, dry",
    "Lentils": "Lentils",
    "Soybeans": "Soybeans",
    "Corn": "Corn for grain",
    "Flaxseed": "Flaxseed",
    "Rye": "Rye, all",
    "Beans": "Beans, all dry (white and coloured)",
    "Canaryseed": "Canary seed",
    "Chick Peas": "Chick peas",
    "Mustard Seed": "Mustard seed",
}
# Crop groups for the delivery-mix chart, bottom to top
GROUPS = {
    "Wheat": ["Wheat"],
    "Durum": ["Amber Durum"],
    "Canola": ["Canola"],
    "Other cereals": ["Barley", "Oats", "Rye", "Corn"],
    "Pulses": ["Peas", "Lentils", "Chick Peas", "Beans"],
    "Other oilseeds & specialty": ["Soybeans", "Flaxseed", "Mustard Seed", "Canaryseed"],
}
PROVINCES = {
    "Alberta": "AB",
    "Alberta & B.C.": "AB",  # 2013-14 .. 2016-17 report AB and BC combined
    "British Columbia": "BC",
    "Saskatchewan": "SK",
    "Manitoba": "MB",
}


def weekly_series(rows: list[tuple], cumulative: bool) -> dict:
    """{grain: {crop_year: [52 values]}} from (grain, crop_year, week, value) rows.

    Cumulative series are carried forward over unreported weeks (the combined
    holiday report skips a week number) up to the last reported week.
    """
    out: dict = {}
    for grain, year, week, value in rows:
        out.setdefault(grain, {}).setdefault(year, [None] * WEEKS)[week - 1] = round(value, 1)
    if cumulative:
        for years in out.values():
            for series in years.values():
                last = max(i for i, v in enumerate(series) if v is not None)
                for i in range(1, last + 1):
                    if series[i] is None:
                        series[i] = series[i - 1]
    return out


def main() -> None:
    con = connect()

    grains = [
        g for (g,) in con.execute(
            f"""select grain from producer_deliveries group by grain
                having count(distinct crop_year) >= {MIN_YEARS}
                order by sum(weekly_kt) desc"""
        ).fetchall()
    ]
    grain_list = ", ".join(f"'{g}'" for g in grains)
    years = [y for (y,) in con.execute("select distinct crop_year from gsw order by 1").fetchall()]

    deliveries = con.execute(
        f"""
        with by_grain as (
            select grain, crop_year, grain_week, sum(cumulative_kt) kt
            from producer_deliveries where grain in ({grain_list}) group by all
        )
        select * from by_grain
        union all
        select '{ALL}', crop_year, grain_week, sum(kt) from by_grain group by all
        """
    ).fetchall()

    # Commercial stocks by location: {grain: {location: {crop_year: [52 week-end values]}}}
    stock_rows = con.execute(
        f"""
        with s as (select * from commercial_stocks where grain in ({grain_list}))
        select grain, location, crop_year, grain_week, kt from s
        union all
        select '{ALL}', location, crop_year, grain_week, sum(kt) from s group by all
        """
    ).fetchall()
    stocks: dict = {}
    for grain, location, year, week, kt in stock_rows:
        stocks.setdefault(grain, {}).setdefault(location, {}).setdefault(year, [None] * WEEKS)[week - 1] = round(kt, 1)

    # Crop-year totals (to date, for the current year) by province and channel.
    splits = con.execute(
        f"""
        with final as (
            select grain, crop_year, province, channel,
                   max_by(cumulative_kt, grain_week) kt
            from producer_deliveries where grain in ({grain_list}) group by all
        )
        select grain, crop_year, province, channel, kt from final
        union all
        select '{ALL}', crop_year, province, channel, sum(kt) from final group by all
        """
    ).fetchall()
    province: dict = {}
    channel: dict = {}
    for grain, year, prov, chan, kt in splits:
        # CGC rarely reports a province for direct-to-processor deliveries, so
        # the province split covers elevator and producer-car deliveries only.
        if chan != "process":
            p = province.setdefault(grain, {}).setdefault(year, {})
            key = PROVINCES.get(prov, "Other")
            p[key] = round(p.get(key, 0) + kt, 1)
        c = channel.setdefault(grain, {}).setdefault(year, {})
        c[chan] = round(c.get(chan, 0) + kt, 1)

    # Exports (terminal + direct) and licensed processing per grain, cumulative, for the flash table
    use_rows = []
    for g in grains:
        for name, year, week, kt in con.execute(flows_sql(g)).fetchall():
            if name.startswith("exports_"):
                use_rows.append(("exports", g, year, week, kt))
            elif name == "process":
                use_rows.append(("process", g, year, week, kt))
    uses = {}
    for kind in ("exports", "process"):
        agg: dict = {}
        for k, g, year, week, kt in use_rows:
            if k == kind:
                agg[(g, year, week)] = agg.get((g, year, week), 0) + kt
        tot: dict = {}
        for (g, year, week), kt in agg.items():
            tot[(year, week)] = tot.get((year, week), 0) + kt
        rows = [(g, y, w, kt) for (g, y, w), kt in agg.items()] + [(ALL, y, w, kt) for (y, w), kt in tot.items()]
        uses[kind] = weekly_series(rows, cumulative=True)

    # Western Canada production by harvest year (kt), to size deliveries against the crop
    wp = external.western_production()
    production = {}
    for grain, crop in STATCAN_CROPS.items():
        if grain in grains and crop in wp.columns:
            production[grain] = {str(y): round(v / 1000, 1) for y, v in wp[crop].dropna().items() if y >= int(years[0][:4]) and v > 0}

    latest_week, week_ending = con.execute(
        "select grain_week, week_ending from gsw where crop_year = ? order by grain_week desc limit 1",
        [years[-1]],
    ).fetchone()

    data = {
        "meta": {
            "current_year": years[-1],
            "latest_week": latest_week,
            "week_ending": week_ending.date().isoformat(),
            "generated": dt.date.today().isoformat(),
        },
        "grains": [ALL, *grains],
        "years": years,
        "deliveries": weekly_series(deliveries, cumulative=True),
        "stocks": stocks,
        "province": province,
        "channel": channel,
        "exports": uses["exports"],
        "process": uses["process"],
        "production": production,
        "groups": GROUPS,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, separators=(",", ":"), allow_nan=False)
    OUT_PATH.write_text(text)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)} ({len(text) / 1e3:.0f} KB, {len(grains)} grains, {len(years)} crop years)")


if __name__ == "__main__":
    main()
