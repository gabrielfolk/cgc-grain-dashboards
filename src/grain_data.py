"""Build the per-grain pipeline pages: dashboard/<slug>/data.json plus a copy of the page template.

Run after src/ingest.py:
    python src/grain_data.py            # all grains
    python src/grain_data.py wheat      # one grain

Each page follows one grain from farm deliveries to its uses (processing, feed, exports)
and commercial stocks. Flows are crop-year-to-date (cumulative) series; stocks are
week-end levels.
    deliveries      producer deliveries (elevators + direct to processors + producer cars)
    process         processed at licensed facilities (Process worksheet, "Milled/Mfg Grain")
    primary_deliveries / primary_shipments
                    grain delivered into and shipped out of licensed country (primary)
                    elevators, all destinations; with country stocks this shows whether
                    elevators are filling up (shipments lagging) or being drawn down
    feed            CGC's domestic feed grain table ("Feed Grains"): primary elevator shipments
                    of grain for domestic feed. A subset of the all-grains figures, so feed
                    grain delivered to elevators is already inside producer deliveries.
    exports_<port>  licensed port terminal exports, ports grouped so all years compare
    exports_direct  shipped from country elevators straight to export destinations or
                    container loaders, bypassing port terminals
    port_<port>     terminal exports by individual port (no zero-fill: Vancouver and Prince
                    Rupert only exist from 2018-19; before that CGC reports Pacific combined)
    receipts_<port> terminal receipts (unloads at port), ports grouped like exports_<port>
    eastern_exports terminal exports graded Canada Eastern (Ontario/Quebec wheat and corn).
                    Kept out of every other terminal series so they cover Western Canadian
                    grain only; shown as a memo line so totals reconcile with CGC's.
    stocks_<site>   commercial stocks at country elevators, processors and port terminals
"""

from __future__ import annotations

import datetime as dt
import json
import html
import re
import sys
from pathlib import Path

import external
from db import EASTERN_GRADE, connect

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "dashboard" / "pipeline.html"
WEEKS = 52
PORTS = {
    "Pacific": "pacific",       # before 2018-19 Vancouver and Prince Rupert are reported together
    "Vancouver": "pacific",
    "Prince Rupert": "pacific",
    "Thunder Bay": "thunder_bay",
    "St. Lawrence": "st_lawrence",
    "Bay & Lakes": "st_lawrence",
    "Churchill": "churchill",
}
DIRECT_EXPORT_REGIONS = ("Export Destinations", "Western Container", "Eastern Container")
# Individual ports for the weekly flash table (Vancouver and Prince Rupert are split from 2018-19)
PORT_SLUGS = {"Vancouver": "vancouver", "Prince Rupert": "prince_rupert", "Pacific": "pacific_combined",
              "Thunder Bay": "thunder_bay", "Bay & Lakes": "bay_lakes", "St. Lawrence": "st_lawrence", "Churchill": "churchill"}

# Links to the cross-crop pages, relative to a crop page (dashboard/<crop>/ on the site)
LINKS = {
    "Overview": "../",
    "Western Feed Grains": "../feed/",
    "Producer Margins": "../margins/",
}
# CGC grain name -> StatCan field crop name (table 32-10-0359), to size deliveries against the crop
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
PROVINCES = {
    "Alberta": "AB",
    "Alberta & B.C.": "AB",  # 2013-14 .. 2016-17 report AB and BC combined
    "British Columbia": "BC",
    "Saskatchewan": "SK",
    "Manitoba": "MB",
}

GRAINS = {
    "canola": {
        "grain": "Canola", "title": "Canola Pipeline", "name": "canola",
        "lede": "Western Canadian canola supply and disappearance from CGC weekly data: producer deliveries, crush, exports by port, terminal receipts and commercial stocks, with a backtested crush and export forecast.",
        "process": {"label": "Crush", "verb": "Crushed", "site": "Crushers"},
        "feed": False, "hero": "process",
        "notes": ["Crush is canola processed by licensed crushers (CGC “Milled/Mfg Grain”)."],
    },
    "wheat": {
        "grain": "Wheat", "title": "Wheat Pipeline", "name": "wheat",
        "lede": "Western Canadian wheat (ex-durum) from CGC weekly data: producer deliveries, exports by port, terminal receipts, licensed milling, domestic feed shipments and commercial stocks.",
        "process": {"label": "Licensed milling", "verb": "Milled at licensed mills", "site": "Mills"},
        "feed": True, "hero": "exports",
        "notes": [
            "Durum is shown on its own page. CGC reports it separately from other wheat classes.",
            "Licensed milling is wheat processed at CGC-licensed process elevators (“Milled/Mfg Grain”). It covers only part of Canadian flour milling, so domestic use is understated here.",
        ],
    },
    "durum": {
        "grain": "Amber Durum", "title": "Durum Pipeline", "name": "durum",
        "lede": "Western Canadian amber durum from CGC weekly data: producer deliveries, exports by port (Pacific, Thunder Bay, St. Lawrence) and direct to the US, terminal receipts and commercial stocks.",
        "process": None, "feed": False, "hero": "exports",
        "notes": ["CGC reports almost no durum processing at licensed facilities (and none before 2018-19), so this page leaves processing out."],
    },
    "barley": {
        "grain": "Barley", "title": "Barley Pipeline", "name": "barley",
        "lede": "Western Canadian barley from CGC weekly data: producer deliveries, exports by port, terminal receipts, malting and processing, domestic feed shipments and commercial stocks.",
        "process": {"label": "Malting & processing", "verb": "Processed", "site": "Processors"},
        "feed": True, "hero": "exports",
        "notes": [
            "Malting & processing is barley processed at CGC-licensed process elevators (“Milled/Mfg Grain”), mostly malt plants.",
            "Most barley is fed on farms or sold directly to feedlots, which CGC does not track.",
        ],
    },
    "peas": {
        "grain": "Peas", "title": "Peas Pipeline", "name": "peas",
        "lede": "Western Canadian dry peas from CGC weekly data: producer deliveries, exports by port and direct, terminal receipts, licensed processing and commercial stocks.",
        "process": {"label": "Processing", "verb": "Processed", "site": "Processors"},
        "feed": False, "hero": "exports",
        "notes": ["Processing is peas handled by CGC-licensed process elevators (“Milled/Mfg Grain”)."],
    },
    "lentils": {
        "grain": "Lentils", "title": "Lentils Pipeline", "name": "lentils",
        "lede": "Western Canadian lentils from CGC weekly data: producer deliveries, exports by port and direct, terminal receipts and commercial stocks.",
        "process": None, "feed": False, "hero": "exports",
        "notes": ["CGC reports almost no lentil processing at licensed facilities, so this page leaves processing out."],
    },
    "oats": {
        "grain": "Oats", "title": "Oats Pipeline", "name": "oats",
        "lede": "Western Canadian oats from CGC weekly data: producer deliveries, licensed milling, exports by port and direct from country elevators, terminal receipts and commercial stocks.",
        "process": {"label": "Milling", "verb": "Milled", "site": "Mills"},
        "feed": False, "hero": "exports",
        "notes": [
            "Milling is oats processed at CGC-licensed process elevators (“Milled/Mfg Grain”), mostly oat mills.",
            "Most oat exports are direct: CGC records them as shipped from country elevators straight to export destinations, not through a port terminal.",
        ],
    },
    "soybeans": {
        "grain": "Soybeans", "title": "Soybeans Pipeline", "name": "soybeans",
        "lede": "Soybeans from CGC weekly data: western producer deliveries, licensed processing, exports by port, terminal receipts and commercial stocks.",
        "process": {"label": "Processing", "verb": "Processed", "site": "Processors"},
        "feed": False, "hero": "exports",
        "notes": ["Soybean grades do not record where the beans were grown. Exports, receipts and stocks at eastern terminals (Bay & Lakes, St. Lawrence) include Ontario and Quebec soybeans, so exports run well above western deliveries. Pacific exports are the better guide to western movement."],
    },
    "corn": {
        "grain": "Corn", "title": "Corn Pipeline", "name": "corn",
        "lede": "Corn from CGC weekly data: western producer deliveries, licensed processing, exports, terminal receipts and commercial stocks.",
        "process": {"label": "Processing", "verb": "Processed", "site": "Processors"},
        "feed": False, "hero": "process",
        "notes": [
            "Processing is corn handled by CGC-licensed process elevators (“Milled/Mfg Grain”); CGC leaves the province blank for much of it.",
            "Corn graded Canada Eastern (CE) is left out of exports and stocks. Corn exported through Bay & Lakes and St. Lawrence under other grades does not record origin; those exports run well above western deliveries, so most of them are not Western Canadian corn.",
        ],
    },
    "flaxseed": {
        "grain": "Flaxseed", "title": "Flaxseed Pipeline", "name": "flaxseed",
        "lede": "Western Canadian flaxseed from CGC weekly data: producer deliveries, licensed processing, exports by port and direct, terminal receipts and commercial stocks.",
        "process": {"label": "Processing", "verb": "Processed", "site": "Processors"},
        "feed": False, "hero": "exports",
        "notes": ["Processing is flaxseed handled by CGC-licensed process elevators (“Milled/Mfg Grain”)."],
    },
    "rye": {
        "grain": "Rye", "title": "Rye Pipeline", "name": "rye",
        "lede": "Western Canadian rye from CGC weekly data: producer deliveries, licensed processing, exports and commercial stocks.",
        "process": {"label": "Processing", "verb": "Processed", "site": "Processors"},
        "feed": False, "hero": "exports",
        "notes": ["Processing is rye handled by CGC-licensed process elevators (“Milled/Mfg Grain”)."],
    },
    "beans": {
        "grain": "Beans", "title": "Beans Pipeline", "name": "beans",
        "lede": "Western Canadian dry beans from CGC weekly data: producer deliveries, licensed processing, direct exports and commercial stocks.",
        "process": {"label": "Processing", "verb": "Processed", "site": "Processors"},
        "feed": False, "hero": "exports",
        "notes": ["CGC reports no bean exports through port terminals. Bean exports are shipped from country elevators, split between containers and direct shipments to export destinations."],
    },
    "canaryseed": {
        "grain": "Canaryseed", "title": "Canaryseed Pipeline", "name": "canaryseed",
        "lede": "Western Canadian canaryseed from CGC weekly data: producer deliveries, exports (mostly direct from country elevators) and commercial stocks.",
        "process": None, "feed": False, "hero": "exports",
        "notes": ["CGC reports no canaryseed processing at licensed facilities, so this page leaves processing out."],
    },
    "chickpeas": {
        "grain": "Chick Peas", "title": "Chickpeas Pipeline", "name": "chickpeas",
        "lede": "Western Canadian chickpeas from CGC weekly data: producer deliveries, direct exports and commercial stocks.",
        "process": None, "feed": False, "hero": "exports",
        "notes": ["CGC reports almost no chickpea processing at licensed facilities and no exports through port terminals. Chickpea exports are shipped from country elevators, mostly by container."],
    },
    "mustard": {
        "grain": "Mustard Seed", "title": "Mustard Pipeline", "name": "mustard seed",
        "lede": "Western Canadian mustard seed from CGC weekly data: producer deliveries, direct exports and commercial stocks.",
        "process": None, "feed": False, "hero": "exports",
        "notes": ["CGC reports no mustard seed processing at licensed facilities and virtually no terminal exports. Mustard exports are shipped from country elevators, split between containers and direct shipments to export destinations."],
    },
}

PAGE_OF = {cfg["grain"]: slug for slug, cfg in GRAINS.items()}


def delivery_splits(con, grains: list[str], total: str | None = None) -> tuple[dict, dict]:
    """Crop-year deliveries (to date, for the current year) by province and by channel.

    Returns ({grain: {crop_year: {province: kt}}}, {grain: {crop_year: {channel: kt}}}).
    With `total`, also adds the sum of `grains` under that name.
    """
    grain_list = ", ".join(f"'{g}'" for g in grains)
    rows = con.execute(
        f"""
        with final as (
            select grain, crop_year, province, channel,
                   max_by(cumulative_kt, grain_week) kt
            from producer_deliveries where grain in ({grain_list}) group by all
        )
        select grain, crop_year, province, channel, kt from final
        """ + (f"union all select '{total}', crop_year, province, channel, sum(kt) from final group by all" if total else "")
    ).fetchall()
    province: dict = {}
    channel: dict = {}
    for grain, year, prov, chan, kt in rows:
        # CGC rarely reports a province for direct-to-processor deliveries, so
        # the province split covers elevator and producer-car deliveries only.
        if chan != "process":
            p = province.setdefault(grain, {}).setdefault(year, {})
            key = PROVINCES.get(prov, "Other")
            p[key] = round(p.get(key, 0) + kt, 1)
        c = channel.setdefault(grain, {}).setdefault(year, {})
        c[chan] = round(c.get(chan, 0) + kt, 1)
    return province, channel


def western_production(grains: list[str], first_year: int) -> dict:
    """{grain: {harvest year: kt}}: StatCan production for MB, SK, AB and BC."""
    wp = external.western_production()
    return {
        g: {str(y): round(v / 1000, 1) for y, v in wp[STATCAN_CROPS[g]].dropna().items() if y >= first_year and v > 0}
        for g in grains if STATCAN_CROPS.get(g) in wp.columns
    }


def flows_sql(grain: str) -> str:
    """Cumulative flows as (series, crop_year, grain_week, kt) rows."""
    return f"""
select 'deliveries', crop_year, grain_week, sum(cumulative_kt)
from producer_deliveries where grain = '{grain}' group by all
union all
select 'process', crop_year, grain_week, sum(ktonnes) from gsw
where grain = '{grain}' and worksheet = 'Process' and metric = 'Milled/Mfg Grain' and period = 'Crop Year'
group by all
union all
select 'primary_' || lower(metric), crop_year, grain_week, sum(ktonnes) from gsw
where grain = '{grain}' and worksheet = 'Primary' and metric in ('Deliveries', 'Shipments') and period = 'Crop Year'
group by all
union all
select 'feed', crop_year, grain_week, sum(ktonnes) from gsw
where grain = '{grain}' and worksheet = 'Feed Grains' and metric = 'Shipments' and period = 'Crop Year'
group by all
union all
select 'exports_' || case {" ".join(f"when region = '{k}' then '{v}'" for k, v in PORTS.items())} end,
       crop_year, grain_week, sum(ktonnes) from gsw
where grain = '{grain}' and worksheet = 'Terminal Exports' and period = 'Crop Year' and not {EASTERN_GRADE}
group by all
union all
select 'port_' || case {" ".join(f"when region = '{k}' then '{v}'" for k, v in PORT_SLUGS.items())} end,
       crop_year, grain_week, sum(ktonnes) from gsw
where grain = '{grain}' and worksheet = 'Terminal Exports' and period = 'Crop Year' and not {EASTERN_GRADE}
group by all
union all
select 'receipts_' || case {" ".join(f"when region = '{k}' then '{v}'" for k, v in PORTS.items())} end,
       crop_year, grain_week, sum(ktonnes) from gsw
where grain = '{grain}' and worksheet = 'Terminal Receipts' and period = 'Crop Year' and not {EASTERN_GRADE}
group by all
union all
select 'eastern_exports', crop_year, grain_week, sum(ktonnes) from gsw
where grain = '{grain}' and worksheet = 'Terminal Exports' and period = 'Crop Year' and {EASTERN_GRADE}
group by all
union all
select 'exports_direct', crop_year, grain_week, sum(ktonnes) from gsw
where grain = '{grain}' and worksheet = 'Primary Shipment Distribution' and metric = 'Shipment Distribution'
  and period = 'Crop Year' and region in {DIRECT_EXPORT_REGIONS}
group by all
"""


# CGC spells the same grade differently across years ("No. 2 CWRS", "No.2 CW RS"); grades are matched
# on letters and digits only, with these aliases on top
GRADE_ALIASES = {"NO1CAN": "NO1CANADA", "CWFEED": "FEEDCW", "OTHERCAN": "OTHER", "ALLGRADESCOMBINED": "OTHER"}
MIN_GRADE_SHARE = 0.03  # a grade gets its own series if it is at least this share of recent exports
MIN_NAMED_SHARE = 0.5   # skip the grade mix when named grades are under half of recent exports
MIN_GRADE_KT = 100      # or when terminal exports average under this many kt a year


def grade_key(grade: str) -> str:
    k = re.sub(r"[^A-Z0-9]", "", grade.upper())
    return GRADE_ALIASES.get(k, k)


def export_grades(con, grain: str, years: list[str]) -> dict:
    """Terminal exports by grade: {"grades": [labels, largest first], "series": {label: {crop_year: [52 cumulative kt]}}}.

    Grades that make up at least MIN_GRADE_SHARE of the last five completed crop years' terminal
    exports keep their own series (labelled with CGC's latest spelling); everything else, including
    grades CGC named in early years but now reports as OTHER, is grouped as "Other grades", so the mix
    compares like with like across years. Western grain only (Canada Eastern grades are left out).
    Series start in the first crop year CGC named any tracked grade (before that, some crops were
    reported only as OTHER or all grades combined). Empty when fewer than two grades are tracked or
    named grades are under MIN_NAMED_SHARE of recent exports, or volumes are too small to matter.
    """
    rows = con.execute(
        f"""
        select grade, crop_year, grain_week, sum(ktonnes) from gsw
        where grain = '{grain}' and worksheet = 'Terminal Exports' and period = 'Crop Year' and not {EASTERN_GRADE}
        group by all
        """
    ).fetchall()
    label = {grade_key(g): g for g, y, w, kt in sorted(rows, key=lambda r: (r[1], r[2]))}  # latest spelling wins
    by_key = to_series([(grade_key(g), y, w, kt) for g, y, w, kt in rows], cumulative=True, add=True)

    def final(k: str, y: str) -> float:
        s = by_key[k].get(y)
        return max((v for v in s if v is not None), default=0) if s else 0

    recent = years[-6:-1]
    total = {k: sum(final(k, y) for y in recent) for k in by_key}
    grand = sum(total.values())
    if grand / len(recent) < MIN_GRADE_KT:
        return {}
    tracked = [k for k in sorted(total, key=total.get, reverse=True) if k != "OTHER" and total[k] / grand >= MIN_GRADE_SHARE]
    other = [k for k in by_key if k not in tracked]
    if not tracked or len(tracked) + bool(other) < 2 or sum(total[k] for k in tracked) / grand < MIN_NAMED_SHARE:
        return {}
    first = next(y for y in years if any(final(k, y) > 0 for k in tracked))
    years = years[years.index(first):]

    series = {label[k]: {y: v for y, v in by_key[k].items() if y in years} for k in tracked}
    if other:
        series["Other grades"] = {
            y: [None if all(by_key[k].get(y, [None] * WEEKS)[i] is None for k in other)
                else round(sum(by_key[k].get(y, [None] * WEEKS)[i] or 0 for k in other), 1) for i in range(WEEKS)]
            for y in years
        }
    order = list(series)
    return {"grades": order, "series": series}


def stocks_sql(grain: str) -> str:
    return f"""
select 'stocks_' || case location when 'country' then 'country' when 'process' then 'process' else 'terminals' end,
       crop_year, grain_week, kt
from commercial_stocks where grain = '{grain}'
"""


def to_series(rows, cumulative: bool, add: bool = False) -> dict:
    """{name: {crop_year: [52 values]}} from (name, crop_year, week, kt) rows; `add` sums rows that share a slot."""
    out: dict = {}
    for name, year, week, kt in rows:
        s = out.setdefault(name, {}).setdefault(year, [None] * WEEKS)
        s[week - 1] = round((s[week - 1] or 0) + kt if add else kt, 1)
    if cumulative:
        # carry cumulative totals over unreported weeks (holiday report), up to the last week
        for years in out.values():
            for s in years.values():
                last = max(i for i, v in enumerate(s) if v is not None)
                for i in range(1, last + 1):
                    if s[i] is None:
                        s[i] = s[i - 1]
    return out


def build(con, slug: str) -> None:
    cfg = GRAINS[slug]
    years = [y for (y,) in con.execute("select distinct crop_year from gsw order by 1").fetchall()]
    flows = to_series(con.execute(flows_sql(cfg["grain"])).fetchall(), cumulative=True)
    stocks = to_series(con.execute(stocks_sql(cfg["grain"])).fetchall(), cumulative=False)
    if not cfg["process"]:
        flows.pop("process", None)
    if not cfg["feed"]:
        flows.pop("feed", None)
    # A flow with nothing in a year has no rows; fill with zeros so sums and charts work.
    for name in [n for n in flows if n != "deliveries" and not n.startswith("port_") and n != "eastern_exports"]:
        for y in years:
            flows[name].setdefault(y, [0.0] * WEEKS)

    province, channel = delivery_splits(con, [cfg["grain"]])
    latest_week, week_ending = con.execute(
        "select grain_week, week_ending from gsw where crop_year = ? order by grain_week desc limit 1", [years[-1]]
    ).fetchone()
    data = {
        "meta": {
            **{k: cfg[k] for k in ("grain", "title", "name", "lede", "process", "feed", "hero", "notes")},
            "slug": slug,
            "links": LINKS,
            "current_year": years[-1],
            "latest_week": latest_week,
            "week_ending": week_ending.date().isoformat(),
            "generated": dt.date.today().isoformat(),
        },
        "years": years,
        "flows": flows,
        "stocks": stocks,
        "province": province.get(cfg["grain"], {}),
        "channel": channel.get(cfg["grain"], {}),
        "production": western_production([cfg["grain"]], int(years[0][:4])).get(cfg["grain"], {}),
        "grades": export_grades(con, cfg["grain"], years),
    }
    out = ROOT / "dashboard" / slug
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, separators=(",", ":"), allow_nan=False)
    (out / "data.json").write_text(text)
    page = TEMPLATE.read_text()
    for key, value in {"TITLE": cfg["title"], "GRAIN": cfg["grain"], "LEDE": cfg["lede"]}.items():
        page = page.replace("{{" + key + "}}", html.escape(value))
    (out / "index.html").write_text(page)
    print(f"Wrote dashboard/{slug}/ ({len(text) / 1e3:.0f} KB): {sorted(flows)}")


def main() -> None:
    con = connect()
    for slug in sys.argv[1:] or GRAINS:
        build(con, slug)


if __name__ == "__main__":
    main()
