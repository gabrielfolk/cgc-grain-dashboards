"""Build dashboard/feed/data.json for the Feed Grains page: our own estimate of domestic feed use by
commodity (barley, wheat, durum, oats, Canadian and US corn), for Western Canada (MB, SK, AB, BC)
and Eastern Canada (Ontario, Quebec and the Atlantic provinces), and how it is built.

Run after src/ingest.py:
    python src/feed_data.py

The estimate is src/feed_model.py: animals x grain fed per head (step 1), split across grains by
price and availability (step 2). This module assembles its inputs and adds step 3, corn by origin.
It takes nothing from any agency's feed figures (StatCan's residual, StatCan's farm survey of grain
fed on farms, CGC's elevator feed shipments); those are comparisons only. Its inputs are animal
numbers, published rations, prices and supply:
    prices          prairie and Ontario farm prices (32-10-0077; PRICE_GEOS), US corn delivered to
                    southern Alberta
    availability    Canada supply over its average (32-10-0013: stocks, production, imports), West only
    elasticities    price and availability: PRICE_ELASTICITY and AVAILABILITY_ELASTICITY, assumptions
    durum split     durum's median share of the wheat and durum grown in the West (32-10-0359)
    western corn    the western corn crop (32-10-0359) + US corn imports into the West (32-10-0014,
                    customs-based), both measured
    corn origin     corn imports by region (corn_origin())
    forage          extra grain fed to cattle when hay is short, West: the Saskatchewan ration guide's
                    barley per tonne of hay replaced (hay_shortfall())
    co-products     distillers' grains and corn gluten feed from grain processed by industry,
                    netted out of each region's grain energy (coproducts())

StatCan's "animal feed, waste and dockage", a residual of its supply and disposition balance
(what is left after exports, processing, seed and stocks, so it also carries waste, dockage and
balancing error), is the page's reference. Published for Canada only, so this module splits it
into regions:
    barley, wheat, durum, oats  each region's on-farm feed (32-10-0015, published by region)
                                plus a share of the rest of Canada's feed use (fed off the farm
                                that grew it) in proportion to the region's production
    corn                        West = domestic use in "other provinces" (32-10-0014, i.e. the West
                                and the Atlantic provinces) less seed and the Atlantic crop (assumed
                                fed where grown); East = the rest of Canada's corn feed.
The regions add up to StatCan's Canada total.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

import external
import feed_model
import inputs
from db import connect

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "dashboard" / "feed" / "data.json"
WEST = ["Manitoba", "Saskatchewan", "Alberta", "British Columbia"]
ATLANTIC = ["New Brunswick", "Nova Scotia", "Prince Edward Island", "Newfoundland and Labrador"]
EAST = ["Ontario", "Quebec"] + ATLANTIC
REGIONS = ["west", "east"]

# StatCan supply-and-disposition crop names -> page keys
SD_CROPS = {"Barley": "barley", "Wheat, excluding durum": "wheat", "Durum wheat": "durum", "Oats": "oats"}
SMALL = ["barley", "wheat", "durum", "oats"]
GRAINS = SMALL + ["corn"]
PROD_NAMES = {"barley": "Barley", "wheat": "Wheat, all excluding durum wheat", "durum": "Wheat, durum", "oats": "Oats",
              "corn": "Corn for grain"}

# Alberta monthly farm prices (C$/t): the feed price of each small grain
PRICE_SERIES = {
    "barley": "Barley for animal feed",
    "wheat": "Wheat (except durum wheat), other",
    "durum": "Durum wheat",
    "oats": "Oats",
}
# Where each western price is taken. Feed wheat is the average of Saskatchewan's and Manitoba's
# "other" (non-milling) wheat: Alberta's "other" wheat has been priced above milling wheat since
# 2021-22 ($300 against $284 in 2025-26), so it no longer tracks feed wheat, while Saskatchewan's
# and Manitoba's stay $40-60 below milling. The rest are Alberta, the feeding centre.
PRICE_GEOS = {"barley": ["Alberta"], "wheat": ["Saskatchewan", "Manitoba"], "durum": ["Alberta"], "oats": ["Alberta"]}
# Ontario monthly farm prices (C$/t), the Eastern benchmark. Ontario corn is the corn price in the model.
EAST_PRICE_SERIES = {"corn": "Corn for grain", "barley": "Barley", "wheat": "Ontario wheat excluding payments", "oats": "Oats"}
# Feeding value relative to barley (cattle energy basis)
ENERGY = feed_model.ENERGY
# US corn delivered to southern Alberta = CBOT + this basis and freight (US$/bu)
CORN_BASIS_USD_BU = 1.60
BU_CORN_PER_T = 39.368
# Substitution elasticity between grains on energy-adjusted price. No published estimate for
# Canadian feeding was found; 1 (shares move in inverse proportion to relative price) is the
# neutral assumption. Fitting it on the farm survey gave no stable sign.
PRICE_ELASTICITY = 1.0
# Availability elasticity: how a grain's share of the ration moves with its supply against normal.
# An assumption, like the price elasticity: 1 means a grain's share moves in proportion to its
# supply over its average. (Until 2026-11-26 it was fitted on StatCan's farm survey of grain fed on
# farms, 1.6; the model now takes no parameter from any agency's feed figures.) Range 0 to 2.
AVAILABILITY_ELASTICITY = 1.0
# Grain fed per tonne of hay short: Saskatchewan Agriculture, Beef Cow Rations and Winter Feeding
# Guidelines (2021), rations for a 1,400 lb cow in mid-pregnancy: 30 lb of alfalfa-grass hay a day,
# or 9 lb of hay + 18 lb of straw + 4 lb of barley. 21 lb of hay is replaced by straw and 4 lb of barley.
HAY_GRAIN_RATE = 4 / 21
# Co-products fed in place of grain: tonnes of distillers' grains (dry-grind ethanol) or corn gluten
# feed (wet milling) per tonne of grain processed. A bushel of corn (56 lb) yields about 17 lb of
# distillers' grains, 0.30 t/t; wheat ethanol is similar. Fed at barley's energy: distillers' grains
# have about corn's energy, but part of what is fed replaces protein meal rather than grain.
COPRODUCT_YIELD = 0.30
COPRODUCT_ENERGY = 1.0


def crop_year_of(date: pd.Timestamp) -> str:
    y = date.year if date.month >= 8 else date.year - 1
    return f"{y}-{y + 1}"


def num(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


# ------------------------------------------------------------------ StatCan

def supply_disposition() -> dict:
    """Crop-year totals (July, cumulative) and in-year partials (Dec, Mar) by crop, kt. Canada only."""
    sd = external.statcan("32100013")
    sd = sd[sd["Type of crop"].isin(SD_CROPS)]
    items = {
        "Animal feed, waste and dockage": "feed", "Production": "production", "Total supplies": "supply",
        "Total beginning stocks": "carry_in", "Total ending stocks": "carry_out", "Total exports": "exports",
        "Imports": "imports", "Human food": "food", "Industrial use": "industrial", "Seed requirements": "seed",
    }
    sd = sd[sd["Supply and disposition of grains"].isin(items)]
    out: dict = {}
    for (crop, item), g in sd.groupby(["Type of crop", "Supply and disposition of grains"]):
        for ref, v in zip(g["REF_DATE"], g["VALUE"]):
            date = pd.Timestamp(ref + "-01")
            cy = crop_year_of(date)
            period = {12: "dec", 3: "mar", 7: "jul"}.get(date.month)
            if period is None or pd.isna(v):
                continue
            out.setdefault(SD_CROPS[crop], {}).setdefault(cy, {}).setdefault(period, {})[items[item]] = round(float(v), 1)
    return out


def farm_feed() -> dict:
    """On-farm feed (kt, full crop year) by grain, crop year and region (StatCan 32-10-0015).

    West uses StatCan's Western Canada total (or the sum of its provinces where that is
    suppressed), East StatCan's Eastern Canada total (Ontario, Quebec and the Maritimes).
    Eastern farms report only "All wheat"; no durum is grown there, so that is their wheat
    (ex-durum) and their durum is zero."""
    fs = external.statcan("32100015")
    fs = fs[(fs["Farm supply and disposition of grains"] == "Animal feed, waste and dockage") & fs["REF_DATE"].str.endswith("-07")]
    val = fs.set_index(["GEO", "Type of crop", "REF_DATE"])["VALUE"]
    get = lambda geo, crop, ref: num(val.get((geo, crop, ref)))

    def total(region_geo, provs, crop, ref):
        v = get(region_geo, crop, ref)
        if v is not None:
            return v
        parts = [get(p, crop, ref) for p in provs]
        return sum(x for x in parts if x is not None) if any(x is not None for x in parts) else None

    out: dict = {}
    for ref in sorted(fs["REF_DATE"].unique()):
        cy = crop_year_of(pd.Timestamp(ref + "-01"))
        for crop, g in SD_CROPS.items():
            east_crop = "All wheat" if g == "wheat" else crop
            reg = {
                "west": total("Western Canada", WEST, crop, ref),
                "east": 0.0 if g == "durum" else total("Eastern Canada", EAST, east_crop, ref),
            }
            if reg["west"] is None:
                continue
            out.setdefault(g, {})[cy] = {r: round(v or 0.0, 1) for r, v in reg.items()}
    return out


def corn() -> dict:
    """Corn (crop year Sep-Aug, cumulative at Dec, Mar and Aug), kt: the Canada balance, the
    Ontario and Quebec crops and exports, and the "other provinces" (West + Atlantic) balance."""
    c = external.statcan("32100014")
    keys = {
        ("Canada", "Animal feed, waste and dockage"): "feed_canada",
        ("Canada", "Production"): "production_canada",
        ("Canada", "Total supplies"): "supply_canada",
        ("Canada", "Total beginning stocks"): "carry_in_canada",
        ("Canada", "Total ending stocks"): "carry_out_canada",
        ("Canada", "Total imports"): "imports_canada",
        ("Canada", "Exports to other countries"): "exports_canada",
        ("Canada", "Human food and industrial use"): "industrial_canada",
        ("Ontario", "Production"): "production_on",
        ("Quebec", "Production"): "production_qc",
        ("Ontario", "Exports to other countries"): "exports_on",
        ("Quebec", "Exports to other countries"): "exports_qc",
        ("Ontario", "Total imports"): "imports_on",  # Ontario has no international line; its imports are all from abroad
        ("Quebec", "Imports from other countries"): "imports_qc",
        ("Other provinces", "Imports from other countries"): "imports_west",
        ("Other provinces", "Production"): "production_other",
        ("Other provinces", "Total domestic disappearance"): "use_other",
        ("Other provinces", "Seed requirements"): "seed_other",
    }
    out: dict = {}
    for (geo, item), g in c.groupby(["GEO", "Supply and disposition of corn"]):
        key = keys.get((geo, item))
        if not key:
            continue
        for ref, v in zip(g["REF_DATE"], g["VALUE"]):
            date = pd.Timestamp(ref + "-01")
            if pd.isna(v):
                continue
            # label the Sep-Aug corn year by the Aug-Jul crop year it mostly overlaps
            cy = crop_year_of(date - pd.DateOffset(months=1))
            period = {12: "dec", 3: "mar", 8: "aug"}.get(date.month)
            if period:
                out.setdefault(cy, {}).setdefault(period, {})[key] = round(float(v), 1)
    return out


def corn_full_year(cornd: dict, cy: str, key: str) -> tuple[float | None, bool]:
    """A corn flow for the full Sep-Aug year. Where August isn't published yet, the March
    figure (Sep-Mar) scaled by the average March-to-August ratio of the last five years.
    Returns (value, estimated)."""
    c = cornd.get(cy, {})
    if key in c.get("aug", {}):
        return c["aug"][key], False
    if c.get("mar", {}).get(key) is not None:
        prior = [k for k in sorted(cornd) if k < cy and key in cornd[k].get("aug", {}) and cornd[k].get("mar", {}).get(key)][-5:]
        if prior:
            return c["mar"][key] * float(np.mean([cornd[k]["aug"][key] / cornd[k]["mar"][key] for k in prior])), True
    return None, False


def production() -> dict:
    """Production (kt) by grain, region and harvest year. East is the sum of its provinces;
    West is Canada less East, so the regions add up to Canada (and suppressed western
    provinces don't drop out). The Atlantic provinces are also kept on their own, for the
    corn split."""
    p = external.statcan("32100359")
    p = p[(p["Harvest disposition"] == "Production (metric tonnes)") & p["Type of crop"].isin(PROD_NAMES.values()) & (p["REF_DATE"] >= 2010)]
    out: dict = {}
    for k, name in PROD_NAMES.items():
        g = p[p["Type of crop"] == name]
        canada = g[g["GEO"] == "Canada"].set_index("REF_DATE")["VALUE"].dropna()
        east = g[g["GEO"].isin(EAST)].groupby("REF_DATE")["VALUE"].sum()
        atl = g[g["GEO"].isin(ATLANTIC)].groupby("REF_DATE")["VALUE"].sum()
        out[k] = {"canada": {}, "west": {}, "east": {}, "atlantic": {}}
        for y, v in canada.items():
            e, a = float(east.get(y, 0.0)), float(atl.get(y, 0.0))
            for r, x in (("canada", v), ("east", e), ("atlantic", a), ("west", v - e)):
                out[k][r][str(y)] = round(x / 1000, 1)
    return out


def regional_feed(sd: dict, farm: dict, cornd: dict, prod: dict, years: list[str]) -> dict:
    """Estimated feed use (kt) by crop year, grain and region; the regions add up to StatCan's
    Canada total. See the module docstring for the method."""
    out: dict = {}
    for cy in years:
        rec: dict = {}
        for g in SMALL:
            canada = sd.get(g, {}).get(cy, {}).get("jul", {}).get("feed")
            f = farm.get(g, {}).get(cy)
            p = {r: prod[g][r].get(cy[:4]) for r in REGIONS}
            if canada is None or f is None or p["west"] is None:
                continue
            on = sum(f.values())
            off = canada - on
            if off < 0 or on <= 0 and off <= 0:
                # on-farm feed exceeds the Canada total (StatCan's tables don't always reconcile): scale it down
                split = {r: canada * f[r] / on if on > 0 else 0.0 for r in REGIONS}
            else:
                ptot = sum(v or 0.0 for v in p.values())
                split = {r: f[r] + off * (p[r] or 0.0) / ptot for r in REGIONS}
            rec[g] = {r: round(v, 1) for r, v in split.items()} | {"on_farm": f}
        feed, est = corn_full_year(cornd, cy, "feed_canada")
        use, _ = corn_full_year(cornd, cy, "use_other")
        seed = cornd.get(cy, {}).get("aug", {}).get("seed_other", 0.0)
        atl = prod["corn"]["atlantic"].get(cy[:4], 0.0)
        if feed is not None and use is not None:
            west = max(0.0, use - seed - atl)
            rec["corn"] = {"west": round(west, 1), "east": round(feed - west, 1), "estimated": est}
        if rec:
            out[cy] = rec
    return out


def prices() -> dict:
    """Monthly Alberta and Ontario farm prices (C$/t) and delivered US corn (C$/t, CBOT + basis/freight)."""
    p = external.statcan("32100077")
    def monthly(geo, name):
        g = p[(p["GEO"] == geo) & (p["Farm products"].str.startswith(name + " [") | (p["Farm products"] == name))]
        uom = g["UOM"].iloc[0] if len(g) else ""
        return {"uom": uom, "values": {r: round(float(v), 2) for r, v in zip(g["REF_DATE"], g["VALUE"]) if not pd.isna(v) and r >= "2012-01"}}
    def averaged(geos, name):
        """Monthly average over the provinces that published the month."""
        parts = [monthly(g, name) for g in geos]
        months = sorted(set().union(*(x["values"] for x in parts)))
        vals = {m: round(float(np.mean([x["values"][m] for x in parts if m in x["values"]])), 2) for m in months}
        return {"uom": parts[0]["uom"], "values": vals}
    out: dict = {key: averaged(PRICE_GEOS[key], name) for key, name in PRICE_SERIES.items()}
    out["ontario"] = {key: monthly("Ontario", name) for key, name in EAST_PRICE_SERIES.items()}
    zc = external.yahoo_weekly("ZC=F")
    fx = external.yahoo_weekly("CAD=X")
    m = pd.DataFrame({"zc": zc, "fx": fx}).sort_index().ffill().dropna()
    monthly_m = m.resample("MS").mean()
    out["cbot_corn"] = {"uom": "US cents per bushel", "values": {d.strftime("%Y-%m"): round(v, 2) for d, v in monthly_m["zc"].items()}}
    out["usdcad"] = {"uom": "CAD per USD", "values": {d.strftime("%Y-%m"): round(v, 4) for d, v in monthly_m["fx"].items()}}
    last = m.iloc[-1]
    out["latest_market"] = {"date": m.index[-1].date().isoformat(), "cbot_corn": round(float(last["zc"]), 2), "usdcad": round(float(last["fx"]), 4)}
    return out


def corn_delivered(zc_cents: float, fx: float, basis_usd_bu: float = CORN_BASIS_USD_BU) -> float:
    return (zc_cents / 100 + basis_usd_bu) * BU_CORN_PER_T * fx


# ------------------------------------------------------------------ price and availability elasticities

def price_basis(series: dict, cy: str) -> str | None:
    """Which months a crop-year price averages, e.g. "Aug 2024-Jul 2025 (12 months)"."""
    y0 = int(cy[:4])
    months = [f"{y0}-{m:02d}" for m in range(8, 13)] + [f"{y0 + 1}-{m:02d}" for m in range(1, 8)]
    have = [m for m in months if m in series]
    if not have:
        return None
    name = lambda m: pd.Timestamp(m + "-01").strftime("%b %Y")
    return f"{name(have[0])}–{name(have[-1])} avg ({len(have)} mo)"


def crop_year_price(series: dict, cy: str) -> float | None:
    """Average of the monthly prices in an Aug-Jul crop year (None if no months are published)."""
    y0 = int(cy[:4])
    months = [f"{y0}-{m:02d}" for m in range(8, 13)] + [f"{y0 + 1}-{m:02d}" for m in range(1, 8)]
    v = [series[m] for m in months if m in series]
    return float(np.mean(v)) if v else None


LCA_RATIONS = ROOT / "reference" / "alberta_beef_lca_rations.csv"
NBSA_RATIONS = ROOT / "reference" / "nbsa_2021_rations.csv"
CALF_FED_SHARE = feed_model.CALF_FED_SHARE  # 45% of western fed cattle in 2021 (Canfax, in the NBSA)
# moisture of the grains as fed (NBSA 2023, Table D-6), to turn its dry-matter rations into tonnes as fed
NBSA_MOISTURE = {"barley": 0.14, "corn": 0.16, "wheat": 0.15, "oats": 0.14, "screenings": 0.12}


def lca_rations() -> dict:
    """Grain per fed animal from the Alberta beef life cycle study's rations (Conestoga-Rovers &
    Associates for Alberta Agriculture, Phase 1, 2010, Appendix D: generic rations for Alberta beef
    cattle from feedlot nutritionist Dwight Karren; reference/alberta_beef_lca_rations.csv). Barley
    as fed, t per head, by system (backgrounding + finishing, steers and heifers averaged), weighted
    by CALF_FED_SHARE; and finishing days, weighted the same way. An input check, not a model input."""
    r = pd.read_csv(LCA_RATIONS)
    lb = 0.45359237 / 1000
    out = {}
    for system in ("calf_fed", "yearling"):
        g = r[r.system == system]
        back = g[g.phase != "finishing"].barley_lb_as_fed.sum()
        fin = {sex: g[(g.phase == "finishing") & (g.sex == sex)] for sex in ("steer", "heifer")}
        out[system] = {"t_per_head": round((back + np.mean([f.barley_lb_as_fed.sum() for f in fin.values()])) * lb, 3),
                       "finishing_days": float(np.mean([f.days.sum() for f in fin.values()])),
                       "out_lb": {sex: int(f.end_lb.max()) for sex, f in fin.items()}}
    w = {"calf_fed": CALF_FED_SHARE, "yearling": 1 - CALF_FED_SHARE}
    out["weighted"] = {"t_per_head": round(sum(w[k] * out[k]["t_per_head"] for k in w), 3),
                       "finishing_days": round(sum(w[k] * out[k]["finishing_days"] for k in w), 1)}
    return out


def nbsa_rations() -> dict:
    """Grain per head from the National Beef Sustainability Assessment's western rations (Groupe AGECO
    for the Canadian Roundtable for Sustainable Beef, 2023, reference year 2021, Appendix D, Tables
    D-2 to D-6: Gowans Feed's typical rations, dry-matter basis; reference/nbsa_2021_rations.csv).
    Grain as fed = dry-matter intake (% of mid-weight body weight) x on-feed days x the grain's share
    of the ration / (1 - moisture). Per fed animal (backgrounding + finishing, steers and heifers
    averaged) by system and weighted by CALF_FED_SHARE; per western cow and bull. t as fed by grain,
    plus barley-equivalent (screenings at barley's energy). An input check and the range ends, not a
    model input."""
    r = pd.read_csv(NBSA_RATIONS)
    lb = 0.45359237 / 1000
    grains = list(NBSA_MOISTURE)
    energy = feed_model.ENERGY | {"screenings": 1.0}

    def per_head(rows) -> dict:
        t = {g: float(sum(x.dmi_pct_bw / 100 * x.mid_lb * x.on_feed_days * x[f"{g}_dm"] / 100 / (1 - NBSA_MOISTURE[g])
                          for _, x in rows.iterrows()) * lb) for g in grains}
        return t

    def avg_sex(g: pd.DataFrame) -> dict:
        by = [per_head(g[g.sex == sx]) for sx in ("steer", "heifer")]
        return {k: (by[0][k] + by[1][k]) / 2 for k in grains}

    def fmt(t: dict) -> dict:
        return {"by_grain": {g: round(v, 3) for g, v in t.items()}, "t_per_head": round(sum(t.values()), 3),
                "beq": round(sum(v * energy[g] for g, v in t.items()), 3)}

    out = {}
    for system in ("calf_fed", "yearling"):
        g = r[r.system == system]
        out[system] = fmt(avg_sex(g)) | {"finishing_days": int(g[g.stage == "finishing"].on_feed_days.max()),
                                         "out_lb": int(g[g.stage == "finishing"].end_lb.max())}
    w = {"calf_fed": CALF_FED_SHARE, "yearling": 1 - CALF_FED_SHARE}
    wt = {k: sum(w[s_] * out[s_]["by_grain"][k] for s_ in w) for k in grains}
    out["weighted"] = fmt(wt) | {"finishing_days": round(sum(w[k] * out[k]["finishing_days"] for k in w), 1)}
    for stage in ("cow", "bull"):
        out[stage] = fmt(per_head(r[r.stage == stage]))
    return out


def model_panel(sd, cornd, pr, regional, years) -> pd.DataFrame:
    """One row per crop year: StatCan feed use, supply and crop-year prices for barley, wheat, durum
    and oats (Canada) and corn fed in the West (the share regression), and corn feed by region."""
    rows = []
    for cy in years:
        rec = {"cy": cy}
        for g in SMALL:
            full = sd.get(g, {}).get(cy, {}).get("jul")
            rec[f"feed_{g}"] = full.get("feed") if full else None
            rec[f"supply_{g}"] = full.get("supply") if full else None
            rec[f"price_{g}"] = crop_year_price(pr[g]["values"], cy)
        rc = regional.get(cy, {}).get("corn")
        rec["feed_corn_west"] = rc["west"] if rc else None
        rec["feed_corn_east"] = rc["east"] if rc else None
        rec["feed_corn"], rec["corn_estimated"] = corn_full_year(cornd, cy, "feed_canada")
        rec["imports_corn_west"], _ = corn_full_year(cornd, cy, "imports_west")
        rec["supply_corn_west"] = None
        zc, fx = crop_year_price(pr["cbot_corn"]["values"], cy), crop_year_price(pr["usdcad"]["values"], cy)
        rec["price_corn_west"] = corn_delivered(zc, fx) if zc and fx else None
        rec["price_corn_ontario"] = crop_year_price(pr["ontario"]["corn"]["values"], cy)
        rows.append(rec)
    return pd.DataFrame(rows).set_index("cy")


def clean_json(o):
    """NaN/inf -> null and numpy scalars -> Python, so the output is valid JSON."""
    if isinstance(o, dict):
        return {str(k): clean_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean_json(v) for v in o]
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else float(o)
    return o


# ------------------------------------------------------------------ demand model

def availability(sd: dict, prod: dict, years: list[str], cur: str) -> tuple[dict, dict]:
    """Supply of each grain over its average, by crop year: the model's availability index, used
    in the West only. Barley, wheat, durum, oats: Canada supply, carry-in + production + imports
    (32-10-0013), since the West grows almost all of them; the current crop year is last July's
    ending stocks + StatCan's latest production estimate + last year's imports. Western corn is 1
    (US corn can always be brought in). The East is left out: the elasticity is fitted on
    Canada-wide shares, which western grain drives, and eastern feeders buy the local crop at
    Ontario prices, which already reflect its size. The average is over the complete crop years.
    Returns (index, supply kt)."""
    last = f"{int(cur[:4]) - 1}-{cur[:4]}"
    supply: dict = {"west": {}}
    for g in SMALL:
        s = {cy: sd[g][cy]["jul"]["supply"] for cy in years if cy != cur and sd.get(g, {}).get(cy, {}).get("jul", {}).get("supply")}
        if prod[g]["canada"].get(cur[:4]) is not None and last in sd.get(g, {}):
            j = sd[g][last]["jul"]
            s[cur] = j["carry_out"] + prod[g]["canada"][cur[:4]] + j.get("imports", 0)
        supply["west"][g] = s
    index = {r: {g: {cy: v / np.mean([x for c, x in s.items() if c != cur]) for cy, v in s.items()} for g, s in gs.items()}
             for r, gs in supply.items()}
    rd = lambda d: {r: {g: {cy: round(v, 3 if d is index else 1) for cy, v in s.items()} for g, s in gs.items()} for r, gs in d.items()}
    return rd(index), rd(supply)


def corn_origin(demand: dict, cornd: dict, prod: dict) -> dict:
    """Split each region's corn fed into Canadian-grown and US corn, by crop year.

    West: US corn = StatCan's imports into the provinces outside Ontario and Quebec (32-10-0014;
    March scaled to the full year until August is out), capped at the West's corn fed; the rest is
    western-grown. Crop years with no import figure yet: western-grown corn = the western crop
    (32-10-0359) x the share of the crop fed in the West over the last three years, and US corn
    fills the rest of the West's corn demand.
    East: US corn = Ontario and Quebec imports as a share of their crop plus imports, applied to
    corn fed in the East (imported corn assumed used like local corn, by feeders and ethanol
    plants alike); years with no figure use the last three years' share."""
    out: dict = {}
    hist_w, hist_e = {}, {}
    for cy in sorted(demand):
        rec = demand[cy]["by_region"]
        cw, ce = rec["west"]["corn"], rec["east"]["corn"]
        crop_w = prod["corn"]["west"].get(cy[:4])
        imp_w, est_w = corn_full_year(cornd, cy, "imports_west")
        ion, _ = corn_full_year(cornd, cy, "imports_on")
        iqc, _ = corn_full_year(cornd, cy, "imports_qc")
        crop_e = prod["corn"]["east"].get(cy[:4], 0.0) - prod["corn"]["atlantic"].get(cy[:4], 0.0)
        o: dict = {}
        if imp_w is not None:
            us = min(imp_w, cw)
            o["west"] = {"us": us, "ca": cw - us, "imports": imp_w, "crop": crop_w, "method": "estimated imports" if est_w else "imports"}
            if crop_w:
                hist_w[cy] = (cw - us) / crop_w
        else:
            prior = [hist_w[k] for k in sorted(hist_w)[-3:]]
            ratio = float(np.mean(prior)) if prior else 1.0
            ca = min(cw, (crop_w or 0) * ratio)
            o["west"] = {"us": cw - ca, "ca": ca, "imports": None, "crop": crop_w, "crop_fed_share": ratio, "method": "projected"}
        if ion is not None and iqc is not None and crop_e:
            share = (ion + iqc) / (crop_e + ion + iqc)
            hist_e[cy] = share
            method = "imports"
        else:
            share = float(np.mean([hist_e[k] for k in sorted(hist_e)[-3:]])) if hist_e else 0.0
            method = "projected"
        o["east"] = {"us": ce * share, "ca": ce * (1 - share), "us_share": share, "imports": None if method == "projected" else ion + iqc,
                     "crop": crop_e or None, "method": method}
        out[cy] = {r: {k: (round(v, 3 if k.endswith("share") else 1) if isinstance(v, float) else v) for k, v in d.items()} for r, d in o.items()}
    return out


def coproducts(sd: dict, cornd: dict, years: list[str]) -> tuple[dict, dict]:
    """Co-products fed in place of grain, barley-equivalent kt by region and crop year.
    East: Canada's corn for food and industrial use (32-10-0014: ethanol, wet milling; almost all
    in Ontario and Quebec; March scaled to the full year until August is out). West: industrial use
    of wheat excluding durum (32-10-0013: prairie ethanol plants). Each x COPRODUCT_YIELD x
    COPRODUCT_ENERGY. A crop year not yet published repeats the latest one. Returns (barley-eq kt,
    grain processed kt)."""
    grain = {"west": {}, "east": {}}
    for cy in years:
        w = sd.get("wheat", {}).get(cy, {}).get("jul", {}).get("industrial")
        e, _ = corn_full_year(cornd, cy, "industrial_canada")
        if w is not None:
            grain["west"][cy] = w
        if e is not None:
            grain["east"][cy] = e
    for r, g in grain.items():
        for cy in years:
            if cy not in g:
                prior = [k for k in sorted(g) if k < cy]
                if prior:
                    g[cy] = g[prior[-1]]
    beq = {r: {cy: v * COPRODUCT_YIELD * COPRODUCT_ENERGY for cy, v in g.items()} for r, g in grain.items()}
    rd = lambda d: {r: {cy: round(v, 1) for cy, v in g.items()} for r, g in d.items()}
    return rd(beq), rd(grain)


def hay_shortfall(demand: dict, years: list[str]) -> tuple[dict, dict]:
    """Western hay shortfall by crop year, kt: (average hay per beef cow - this year's) x beef cows,
    zero when hay is at or above average. Tame hay production, MB, SK, AB, BC (32-10-0359), harvested
    in the crop year's first calendar year; beef cows from the model's animal numbers. A year whose
    hay isn't published yet repeats the latest year's hay per cow. Returns (shortfall kt, detail)."""
    p = external.statcan("32100359")
    h = p[(p["Type of crop"] == "Tame hay") & (p["Harvest disposition"] == "Production (metric tonnes)") & p["GEO"].isin(WEST)].dropna(subset=["VALUE"])
    n = h.groupby("REF_DATE")["GEO"].nunique()
    hay = (h.groupby("REF_DATE")["VALUE"].sum() / 1000)[n == len(WEST)]
    per_cow, detail = {}, {}
    for cy in years:
        cows = demand[cy]["drivers"]["west"]["Beef Cows"]
        y = int(cy[:4])
        src = y if y in hay.index else max(i for i in hay.index if i < y)
        per_cow[cy] = hay[src] / (demand[cy]["drivers"]["west"]["Beef Cows"] if src == y else
                                  demand[f"{src}-{src + 1}"]["drivers"]["west"]["Beef Cows"])
        detail[cy] = {"hay_kt": round(float(hay[src]) * (cows / demand[f"{src}-{src + 1}"]["drivers"]["west"]["Beef Cows"] if src != y else 1), 1),
                      "hay_year": int(src), "beef_cows": cows}
    avg = float(np.mean([v for cy, v in per_cow.items() if int(cy[:4]) in hay.index]))
    out = {cy: max(0.0, (avg - v) * demand[cy]["drivers"]["west"]["Beef Cows"]) for cy, v in per_cow.items()}
    for cy in years:
        detail[cy] |= {"t_per_cow": round(per_cow[cy], 3), "avg_t_per_cow": round(avg, 3), "shortfall_kt": round(out[cy], 1)}
    return out, detail


def western_corn(cornd: dict, prod: dict, years: list[str]) -> dict:
    """Corn fed in the West, kt: the western corn crop (MB, SK, AB, BC; 32-10-0359, harvested in the
    crop year's first year) + US corn imported into the provinces outside Ontario and Quebec over the
    Sep-Aug corn year (32-10-0014, customs-based; March scaled to the full year until August is out).
    Both are measured. Stock changes and the little western corn used by industry are left out.
    Crop years whose imports aren't published yet are left to the model's projection."""
    out = {}
    for cy in years:
        imp, _ = corn_full_year(cornd, cy, "imports_west")
        crop = prod["corn"]["west"].get(cy[:4])
        if imp is not None and crop is not None:
            out[cy] = crop + imp
    return out


def durum_shares(prod: dict) -> dict:
    """Durum's share (tonnes) of the wheat and durum grown in the West (StatCan production,
    32-10-0359), by harvest year from 2012. Feed durum is mostly grain that missed milling grades,
    so the base assumption is that it is fed in proportion to what is grown."""
    yrs = sorted(y for y in prod["durum"]["west"] if y >= "2012" and y in prod["wheat"]["west"])
    return {f"{y}-{int(y) + 1}": prod["durum"]["west"][y] / (prod["durum"]["west"][y] + prod["wheat"]["west"][y]) for y in yrs}


def durum_share(prod: dict) -> tuple[float, list[str]]:
    """The median of durum_shares(). Returns (share, [first, last crop year])."""
    sh = durum_shares(prod)
    return float(np.median(list(sh.values()))), [min(sh), max(sh)]


def model_demand(pr: dict, years: list[str], corn_west: dict, durum: float, avail: dict, sigma: float, alpha: float, cop: dict,
                 forage: dict | None = None, basis_usd_bu: float = CORN_BASIS_USD_BU,
                 feedlot_wheat_sigma: float = feed_model.FEEDLOT_WHEAT_SIGMA,
                 days_on_feed_west: float | None = feed_model.DAYS_ON_FEED_WEST,
                 west_cow_t: float | None = None, west_bull_t: float = feed_model.WEST_BULL_BARLEY_T) -> dict:
    """Feed demand from animal numbers (src/feed_model.py), priced off each region's grains:
    West = Alberta farm prices and US corn delivered to southern Alberta; East = Ontario.
    durum: durum's base share of the West's wheat and durum (see durum_share); avail: the
    availability index (see availability); sigma, alpha: price and availability elasticities."""
    years = sorted(set(years))
    us = {}
    for cy in years:
        zc, fx = crop_year_price(pr["cbot_corn"]["values"], cy), crop_year_price(pr["usdcad"]["values"], cy)
        if zc and fx:
            us[cy] = corn_delivered(zc, fx, basis_usd_bu)
    series = {"west": {g: pr[g]["values"] for g in ("barley", "wheat", "durum", "oats")},
              "east": {g: pr["ontario"][g]["values"] for g in ("corn", "barley", "wheat", "oats")}}
    prices = {r: {g: {cy: v for cy in years if (v := crop_year_price(s, cy))} for g, s in gs.items()} for r, gs in series.items()}
    basis = {r: {g: {cy: price_basis(s, cy) for cy in years} for g, s in gs.items()} for r, gs in series.items()}
    # a crop year with no farm prices yet (StatCan runs about two months behind) takes the latest month
    cur = years[-1]
    for r, gs in series.items():
        for g, s in gs.items():
            if cur not in prices[r][g] and s:
                last = list(s)[-1]
                prices[r][g][cur] = s[last]
                basis[r][g][cur] = f"{pd.Timestamp(last + '-01').strftime('%b %Y')} (latest month)"
    prices["west"]["corn"] = us
    basis["west"]["corn"] = {cy: price_basis(pr["cbot_corn"]["values"], cy) for cy in years}
    out = feed_model.demand([int(cy[:4]) for cy in years], prices, corn_west, durum, avail, sigma, alpha, cop, forage,
                            feedlot_wheat_sigma, days_on_feed_west, west_cow_t, west_bull_t)
    r1 = lambda d: {k: round(v, 1) for k, v in d.items()}
    r4 = lambda d: {k: round(v, 4) for k, v in d.items()}
    return {cy: {"by_region": {r: r1(v) for r, v in rec["by_region"].items()},
                 "by_group": {r: r1(v) for r, v in rec["by_group"].items()},
                 "beq": r1(rec["beq"]), "beq_gross": r1(rec["beq_gross"]), "coproducts": r1(rec["coproducts"]), "relative_price": rec["relative_price"], "availability": rec["availability"],
                 "base_mix": {r: r4(v) for r, v in rec["base_mix"].items()},
                 "prices": {r: {g: round(prices[r][g][cy], 1) for g in prices[r] if cy in prices[r][g]} for r in prices},
                 "price_basis": {r: {g: basis[r][g][cy] for g in basis[r]} for r in basis},
                 "drivers": rec["drivers"], "meta": rec["meta"], "corn_west": rec["corn_west"]}
            for cy, rec in out.items()}


# ------------------------------------------------------------------ main

# The judgment inputs and their plausible ranges (low, high) for the uncertainty range. Each is
# moved on its own, the rest held at base; see uncertainty().
def ranges(durum_q: tuple) -> dict:
    hay_full = (0.600 * 0.874) / (0.831 * 0.885)  # alfalfa-grass hay vs barley, TDN x dry matter (Saskatchewan guide)
    return {
        "price_elasticity": {"label": "Price elasticity", "base": PRICE_ELASTICITY, "low": 0.5, "high": 1.5,
                             "why": "an assumption; no published Canadian estimate"},
        "alpha": {"label": "Availability elasticity", "base": AVAILABILITY_ELASTICITY, "low": 0.0, "high": 2.0,
                  "why": "an assumption; no published Canadian estimate"},
        "hay_rate": {"label": "Grain per t of hay short", "base": HAY_GRAIN_RATE, "low": 0.0, "high": hay_full,
                     "why": "straw covers it all, to grain replacing all of the hay's energy"},
        "coproduct_yield": {"label": "Co-product yield, t/t processed", "base": COPRODUCT_YIELD, "low": COPRODUCT_YIELD * 0.8,
                            "high": COPRODUCT_YIELD * 1.2, "why": "± 20%"},
        "durum_share": {"label": "Durum share of western wheat feed", "base": None, "low": durum_q[0], "high": durum_q[2],
                        "why": "middle half of the years' western production shares"},
        "feedlot_wheat_sigma": {"label": "Western feedlot wheat price elasticity", "base": feed_model.FEEDLOT_WHEAT_SIGMA,
                                "low": 1.0, "high": 8.0, "why": "an assumption: from the general elasticity to twice the base"},
        "days_on_feed_west": {"label": "Western feedlot grain per fed animal, as days on feed", "base": round(feed_model.DAYS_ON_FEED_WEST, 1),
                              "low": None, "high": None, "why": None},
        "west_cow_grain": {"label": "Western beef cow grain, t a head", "base": None, "low": None,
                           "high": feed_model.WEST_COW_GUIDE_T[1], "why": None},
        "west_bull_grain": {"label": "Western bull grain, t a head", "base": round(feed_model.WEST_BULL_BARLEY_T, 4), "low": None,
                            "high": round(feed_model.WEST_BULL_BARLEY_T, 4), "why": None},
        "corn_basis": {"label": "US corn basis + freight, US$/bu", "base": CORN_BASIS_USD_BU, "low": CORN_BASIS_USD_BU - 0.4,
                       "high": CORN_BASIS_USD_BU + 0.4, "why": "an assumption, ± US$0.40"},
    }


def totals(demand: dict) -> dict:
    """{cy: {region: {grain: kt}}} for the grains, corn by origin and the total; regions + Canada."""
    keys = GRAINS + ["corn_ca", "corn_us"]
    out = {}
    for cy, rec in demand.items():
        b = rec["by_region"]
        reg = {r: {k: b[r].get(k, 0.0) for k in keys} | {"total": sum(b[r][g] for g in GRAINS)} for r in REGIONS}
        reg["canada"] = {k: reg["west"][k] + reg["east"][k] for k in reg["west"]}
        out[cy] = reg
    return out


def uncertainty(base: dict, runs: dict) -> tuple[dict, dict]:
    """Range per crop year, region and grain: base - sqrt(sum of squared falls) to base + sqrt(sum of
    squared rises) over the one-at-a-time runs, treating the inputs as independent. Also each
    input's (low run, high run) values, for the sensitivity table."""
    rng, sens = {}, {}
    for cy, regs in base.items():
        rng[cy] = {}
        for r, gs in regs.items():
            rng[cy][r] = {}
            for g, v in gs.items():
                d = [run[cy][r][g] - v for pair in runs.values() for run in pair]
                up, dn = np.sqrt(sum(x * x for x in d if x > 0)), np.sqrt(sum(x * x for x in d if x < 0))
                rng[cy][r][g] = [round(v - dn, 1), round(v + up, 1)]
        sens[cy] = {k: {r: {g: [round(pair[0][cy][r][g], 1), round(pair[1][cy][r][g], 1)] for g in regs[r]} for r in regs} for k, pair in runs.items()}
    return rng, sens


def cgc_feed_shipments(con, years: list[str]) -> tuple[dict, dict]:
    """CGC's feed-grade grain shipped from licensed primary elevators for domestic feed (Grain
    Statistics Weekly, Feed Grains table, MB, SK, AB and BC), kt by crop year and grain, at each
    year's last reported week. A comparison for the page only, not a model input. Returns
    ({cy: {grain: kt}}, {cy: last week})."""
    names = {"Barley": "barley", "Wheat": "wheat", "Amber Durum": "durum", "Oats": "oats", "Corn": "corn"}
    rows = con.execute("""
        with f as (select crop_year, grain::varchar as grain, grain_week, ktonnes from gsw
                   where worksheet = 'Feed Grains' and metric = 'Shipments' and period = 'Crop Year'),
             last as (select crop_year, max(grain_week) as wk from gsw group by crop_year)
        select f.crop_year, f.grain, f.grain_week, sum(f.ktonnes) from f
        join last on f.crop_year = last.crop_year and f.grain_week = last.wk
        group by all""").fetchall()
    out, week = {}, {}
    for cy, grain, wk, kt in rows:
        if cy in years and grain in names:
            out.setdefault(cy, {})[names[grain]] = round(kt, 1)
            week[cy] = wk
    return out, week


def main() -> None:
    con = connect()
    years = [y for (y,) in con.execute("select distinct crop_year from gsw order by 1").fetchall()]
    cur = years[-1]
    sd, cornd, prod, pr, farm = supply_disposition(), corn(), production(), prices(), farm_feed()
    all_years = sorted(set(years) | {cy for g in sd.values() for cy in g})
    all_years = [y for y in all_years if y >= "2012-2013"]
    regional = regional_feed(sd, farm, cornd, prod, all_years)
    panel = model_panel(sd, cornd, pr, regional, all_years)
    # the grain mix: share ~ price ^ -PRICE_ELASTICITY x availability ^ AVAILABILITY_ELASTICITY (both assumptions)
    sigma, alpha = PRICE_ELASTICITY, AVAILABILITY_ELASTICITY
    durum, durum_years = durum_share(prod)
    avail, supply = availability(sd, prod, all_years + [cur], cur)
    cop, processed = coproducts(sd, cornd, all_years + [cur])
    corn_w = western_corn(cornd, prod, all_years)
    yrs = all_years + [cur]
    # forage: hay shortfall needs the model's beef cow numbers, so run once without it
    short, hay = hay_shortfall(model_demand(pr, yrs, corn_w, durum, avail, sigma, alpha, cop), yrs)

    def run(sigma=sigma, alpha=alpha, hay_rate=HAY_GRAIN_RATE, cop_yield=COPRODUCT_YIELD, durum=durum, basis=CORN_BASIS_USD_BU,
            wheat_sigma=feed_model.FEEDLOT_WHEAT_SIGMA, dof_west=feed_model.DAYS_ON_FEED_WEST, cow=None,
            bull=feed_model.WEST_BULL_BARLEY_T):
        """One full model run (steps 1-3) at the given judgment inputs."""
        av = {r: {g: s_ for g, s_ in gs.items()} for r, gs in avail.items()}
        c = {r: {cy: v * cop_yield / COPRODUCT_YIELD for cy, v in g.items()} for r, g in cop.items()}
        d = model_demand(pr, yrs, corn_w, durum, av, sigma, alpha, c, {"west": {cy: hay_rate * v for cy, v in short.items()}}, basis,
                         wheat_sigma, dof_west, cow, bull)
        for cy, o in corn_origin(d, cornd, prod).items():
            for r in REGIONS:
                d[cy]["by_region"][r]["corn_ca"] = o[r]["ca"]
                d[cy]["by_region"][r]["corn_us"] = o[r]["us"]
            d[cy]["corn_origin"] = o
        return d

    demand = run()
    cgc_feed = cgc_feed_shipments(con, yrs)
    R = ranges(tuple(np.percentile(list(durum_shares(prod).values()), [25, 50, 75])))
    R["durum_share"]["base"] = durum
    lca, nbsa = lca_rations(), nbsa_rations()
    coef = feed_model.coefficients()
    cow99 = {p: sum(coef[p]["Beef Cows"][g] * feed_model.ENERGY[g] for g in feed_model.STUDY_GRAINS) for p in feed_model.WEST}
    t_fed = {cy: rec["by_group"]["west"].get("feedlot", 0) / rec["drivers"]["west"]["fed_marketed"]
             for cy, rec in demand.items() if rec["drivers"]["west"].get("fed_marketed")}
    # the NBSA's reference year 2021 spans two crop years
    t21 = np.mean([t_fed[cy] for cy in ("2020-2021", "2021-2022")])
    DF = R["days_on_feed_west"]
    DF["low"] = demand[cur]["meta"]["days_on_feed_inventory"]["west"]
    DF["high"] = round(feed_model.DAYS_ON_FEED_WEST * nbsa["weighted"]["beq"] / t21, 1)
    DF["why"] = (f"{DF['low']:.0f} days from StatCan's cattle-on-feed inventories (low) to {DF['high']:.0f}, the days that give the "
                f"National Beef Sustainability Assessment's 2021 rations, {nbsa['weighted']['beq']:.2f} t of grain per western fed animal "
                f"against the model's {t21:.2f} t (high); the Alberta beef life cycle study's 2010 rations ({lca['weighted']['t_per_head']:.2f} t) "
                f"sit near the base")
    C = R["west_cow_grain"]
    C["low"] = nbsa["cow"]["beq"]
    C["why"] = (f"{C['low'] * 1000:.0f} kg (National Beef Sustainability Assessment, 2021 western cow ration) to {C['high'] * 1000:.0f} kg "
                f"(Manitoba's 2027 silage-ration guide) for every western cow; base the 1999 provincial rates "
                + ", ".join(f"{p} {v * 1000:.0f}" for p, v in cow99.items()) + " kg")
    B = R["west_bull_grain"]
    B["low"] = nbsa["bull"]["beq"]
    B["why"] = (f"{B['low'] * 1000:.0f} kg (National Beef Sustainability Assessment, 2021 western bull ration) to the base, "
                f"{B['base'] * 1000:.0f} kg (Manitoba's 2027 guides)")
    arg = {"price_elasticity": "sigma", "alpha": "alpha", "hay_rate": "hay_rate", "coproduct_yield": "cop_yield",
           "durum_share": "durum", "corn_basis": "basis", "feedlot_wheat_sigma": "wheat_sigma", "days_on_feed_west": "dof_west",
           "west_cow_grain": "cow", "west_bull_grain": "bull"}
    runs = {k: (totals(run(**{arg[k]: v["low"]})), totals(run(**{arg[k]: v["high"]}))) for k, v in R.items()}
    rng, sens = uncertainty(totals(demand), runs)
    for cy in demand:
        demand[cy]["range"] = rng[cy]

    data = {
        "meta": {"current_year": cur, "generated": dt.date.today().isoformat(),
                 "inputs": [
                     inputs.statcan("StatCan livestock and poultry", *feed_model.LIVESTOCK_TABLES),
                     inputs.aafc_slaughter(),
                     inputs.ers_cattle(),
                     inputs.statcan("StatCan grain supply and disposition", "32100013", "32100014"),
                     inputs.statcan("StatCan farm supply and disposition", "32100015"),
                     inputs.production(),
                     inputs.statcan("StatCan farm prices", "32100077"),
                     inputs.markets("CBOT corn and USD/CAD", "ZC=F", "CAD=X"),
                 ],
                 "assumptions": {"energy_vs_barley": ENERGY, "corn_basis_usd_bu": CORN_BASIS_USD_BU, "bu_corn_per_t": BU_CORN_PER_T},
                 "price_series": {"west": PRICE_SERIES, "east": EAST_PRICE_SERIES},
                 "price_geos": {"west": PRICE_GEOS, "east": {g: ["Ontario"] for g in EAST_PRICE_SERIES}}},
        "regional_feed": regional,
        "cgc_feed_shipments": {"kt": cgc_feed[0], "week": cgc_feed[1]},
        "lca_check": lca | {"calf_fed_share": CALF_FED_SHARE, "model_t_per_fed": {cy: round(v, 3) for cy, v in t_fed.items()}},
        "nbsa_check": nbsa | {"calf_fed_share": CALF_FED_SHARE, "model_t_per_fed_2021": round(float(t21), 3)},
        "prices": pr,
        "demand_model": demand,
        "demand_model_meta": {"west_cattle_mix": feed_model.WEST_CATTLE_MIX,
                              "feedlot_lb_day": feed_model.FEEDLOT_BARLEY_LB_DAY, "background_lb_day": feed_model.BACKGROUND_BARLEY_LB_DAY,
                              "background_days": feed_model.BACKGROUND_DAYS, "hog_factors": feed_model.HOG_FACTORS,
                              "feedlot_wheat_sigma": feed_model.FEEDLOT_WHEAT_SIGMA, "feedlot_wheat_cap": feed_model.FEEDLOT_WHEAT_CAP,
                              "days_on_feed_west": round(feed_model.DAYS_ON_FEED_WEST, 1),
                              "west_bull_barley_t": round(feed_model.WEST_BULL_BARLEY_T, 3),
                              "west_cow_1999_t": {p: round(v, 3) for p, v in cow99.items()},
                              "west_cow_guide_t": feed_model.WEST_COW_GUIDE_T, "calf_fed_share": CALF_FED_SHARE, "west_heifer_barley_t": round(feed_model.WEST_HEIFER_BARLEY_T, 3),
                              "durum_share_west": round(durum, 4), "durum_share_years": durum_years,
                              "sigma": round(sigma, 3), "alpha": round(alpha, 3), "fed_share": feed_model.FED_SHARE,
                              "energy": feed_model.ENERGY, "supply": supply,
                              "coproduct_yield": COPRODUCT_YIELD, "coproduct_energy": COPRODUCT_ENERGY, "processed": processed,
                              "hay_grain_rate": round(HAY_GRAIN_RATE, 4), "hay": hay, "price_elasticity": PRICE_ELASTICITY,
                              "corn_west_measured": {cy: round(v, 1) for cy, v in corn_w.items()}},
        "uncertainty": {"inputs": {k: {kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in R.items()},
                        "sensitivity": {cur: sens[cur]}},
        "panel": panel.round(1).reset_index().to_dict(orient="records"),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(clean_json(data), separators=(",", ":"), allow_nan=False)
    OUT_PATH.write_text(text)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)} ({len(text) / 1e3:.0f} KB)")
    print(f"elasticities (assumed): price {sigma:.2f}, availability {alpha:.2f}; durum share {durum:.3f}; hay shortfall:",
          {cy: hay[cy]['shortfall_kt'] for cy in list(hay)[-4:]})
    dm = demand[cur]
    print("range, Canada:", {g: dm["range"]["canada"][g] for g in ["total"] + GRAINS})
    for r in REGIONS:
        print(f"{cur} {r}:", {g: round(dm['by_region'][r][g]) for g in GRAINS + ['corn_ca', 'corn_us']})
    print("meta:", dm["meta"])


if __name__ == "__main__":
    main()
