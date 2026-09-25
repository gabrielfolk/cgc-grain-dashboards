"""Build the per-grain pipeline pages: dashboard/<slug>/data.json plus a copy of the page template.

Run after src/ingest.py:
    python src/grain_data.py            # all grains
    python src/grain_data.py wheat      # one grain

Each page follows one grain from farm deliveries to its uses (processing, feed, exports)
and commercial stocks. Flows are crop-year-to-date (cumulative) series; stocks are
week-end levels.
    deliveries      producer deliveries (elevators + direct to processors + producer cars)
    process         processed at licensed facilities (Process worksheet, "Milled/Mfg Grain")
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
import sys
from pathlib import Path

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

# Published page URLs, for cross-links between pages.
LINKS = {
    "Prairie Delivery Pace": "https://claude.ai/artifact/Mv4x8SXoKdvg8o5mj19TMf",
    "Canola Pipeline": "https://claude.ai/artifact/L9q7H7aYtaMgHopbBbEtjk",
    "Wheat Pipeline": "https://claude.ai/artifact/7ihCwx3TgUNdg2yfSKKyG4",
    "Durum Pipeline": "https://claude.ai/artifact/GUgDjqNEfP8vHb1HrKi2uH",
    "Barley Pipeline": "https://claude.ai/artifact/U6tuj2LiEW7VGqnQLQYwVa",
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


def stocks_sql(grain: str) -> str:
    return f"""
select 'stocks_' || case location when 'country' then 'country' when 'process' then 'process' else 'terminals' end,
       crop_year, grain_week, kt
from commercial_stocks where grain = '{grain}'
"""


def to_series(rows, cumulative: bool) -> dict:
    out: dict = {}
    for name, year, week, kt in rows:
        out.setdefault(name, {}).setdefault(year, [None] * WEEKS)[week - 1] = round(kt, 1)
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

    latest_week, week_ending = con.execute(
        "select grain_week, week_ending from gsw where crop_year = ? order by grain_week desc limit 1", [years[-1]]
    ).fetchone()
    data = {
        "meta": {
            **{k: cfg[k] for k in ("grain", "title", "name", "lede", "process", "feed", "hero", "notes")},
            "slug": slug,
            "links": {t: u for t, u in LINKS.items() if t != cfg["title"]},
            "current_year": years[-1],
            "latest_week": latest_week,
            "week_ending": week_ending.date().isoformat(),
            "generated": dt.date.today().isoformat(),
        },
        "years": years,
        "flows": flows,
        "stocks": stocks,
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
