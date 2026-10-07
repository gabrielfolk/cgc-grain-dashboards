"""Build dashboard/feed/data.json for the Feed Grains page: our own estimate of domestic feed use by
commodity (barley, wheat, durum, oats, Canadian and US corn), for Western Canada (MB, SK, AB, BC)
and Eastern Canada (Ontario, Quebec and the Atlantic provinces), and how it is built.

Run after src/ingest.py:
    python src/feed_data.py

The estimate is src/feed_model.py: animals x grain fed per head (step 1), split across grains by
price and availability (step 2). This module assembles its inputs and adds step 3, corn by origin.
None of it uses StatCan's feed residual, which is a balancing item, not a feed estimate:
    prices          Alberta and Ontario farm prices (32-10-0077), US corn delivered to southern Alberta
    availability    Canada supply over its average (32-10-0013: stocks, production, imports), West only
    elasticities    price: PRICE_ELASTICITY (an assumption); availability: fitted on StatCan's farm
                    survey of grain fed on farms in the West (32-10-0015), not the residual
                    (fit_elasticities())
    durum split     durum's median share of western on-farm wheat and durum feed, same survey
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
    out: dict = {key: monthly("Alberta", name) for key, name in PRICE_SERIES.items()}
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

def crop_year_price(series: dict, cy: str) -> float | None:
    """Average of the monthly prices in an Aug-Jul crop year (None if no months are published)."""
    y0 = int(cy[:4])
    months = [f"{y0}-{m:02d}" for m in range(8, 13)] + [f"{y0 + 1}-{m:02d}" for m in range(1, 8)]
    v = [series[m] for m in months if m in series]
    return float(np.mean(v)) if v else None


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


def fit_elasticities(panel: pd.DataFrame, farm: dict, cur: str) -> dict:
    """The grain mix's availability elasticity, fitted on StatCan's farm survey (grain fed on the
    farm that grew it, West, 32-10-0015; reported by farmers, not a balancing residual), with the
    price elasticity fixed at PRICE_ELASTICITY:
        log(s_i / s_barley) + sigma x log(price ratio) = grain constant + a x log(availability ratio)
    over wheat, durum and oats against barley. Availability is supply (carry-in + production +
    imports) over its own average."""
    grains = ["barley", "wheat", "durum", "oats"]
    done = [cy for cy in panel.index if cy != cur and panel.loc[cy, [f"supply_{g}" for g in grains] + [f"price_{g}" for g in grains]].notna().all()
            and all(farm.get(g, {}).get(cy, {}).get("west", 0) > 0 for g in grains)]
    means = {g: panel.loc[done, f"supply_{g}"].mean() for g in grains}
    X, y = [], []
    for cy in done:
        r = panel.loc[cy]
        for i, g in enumerate(grains[1:]):
            a = np.log((r[f"supply_{g}"] / means[g]) / (r["supply_barley"] / means["barley"]))
            p = np.log((r[f"price_{g}"] / ENERGY[g]) / (r["price_barley"] / ENERGY["barley"]))
            X.append([1.0 if j == i else 0.0 for j in range(3)] + [a])
            y.append(np.log(farm[g][cy]["west"] / farm["barley"][cy]["west"]) + PRICE_ELASTICITY * p)
    X, y = np.array(X), np.array(y)
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ b
    se = np.sqrt(np.diag(e @ e / (len(y) - X.shape[1]) * np.linalg.inv(X.T @ X)))
    return {"a": float(b[-1]), "a_se": float(se[-1]), "b": -PRICE_ELASTICITY, "train_years": [done[0], done[-1]], "n": len(y)}


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


def durum_share(farm: dict) -> tuple[float, list[str]]:
    """Durum's median share (tonnes) of the wheat and durum fed on western farms (StatCan's farm
    survey, 32-10-0015); the median keeps out quality years like 2016-17. Returns (share, [first,
    last crop year])."""
    yrs = sorted(cy for cy in farm.get("durum", {}) if cy in farm.get("wheat", {}) and farm["wheat"][cy]["west"] + farm["durum"][cy]["west"] > 0)
    shares = [farm["durum"][cy]["west"] / (farm["wheat"][cy]["west"] + farm["durum"][cy]["west"]) for cy in yrs]
    return float(np.median(shares)), [yrs[0], yrs[-1]]


def model_demand(pr: dict, years: list[str], corn_west: dict, durum: float, avail: dict, sigma: float, alpha: float, cop: dict,
                 forage: dict | None = None) -> dict:
    """Feed demand from animal numbers (src/feed_model.py), priced off each region's grains:
    West = Alberta farm prices and US corn delivered to southern Alberta; East = Ontario.
    durum: durum's base share of the West's wheat and durum (see durum_share); avail: the
    availability index (see availability); sigma, alpha: price and availability elasticities."""
    years = sorted(set(years))
    us = {}
    for cy in years:
        zc, fx = crop_year_price(pr["cbot_corn"]["values"], cy), crop_year_price(pr["usdcad"]["values"], cy)
        if zc and fx:
            us[cy] = corn_delivered(zc, fx)
    series = {"west": {g: pr[g]["values"] for g in ("barley", "wheat", "durum", "oats")},
              "east": {g: pr["ontario"][g]["values"] for g in ("corn", "barley", "wheat", "oats")}}
    prices = {r: {g: {cy: v for cy in years if (v := crop_year_price(s, cy))} for g, s in gs.items()} for r, gs in series.items()}
    # a crop year with no farm prices yet (StatCan runs about two months behind) takes the latest month
    cur = years[-1]
    for r, gs in series.items():
        for g, s in gs.items():
            if cur not in prices[r][g] and s:
                prices[r][g][cur] = list(s.values())[-1]
    prices["west"]["corn"] = us
    out = feed_model.demand([int(cy[:4]) for cy in years], prices, corn_west, durum, avail, sigma, alpha, cop, forage)
    r1 = lambda d: {k: round(v, 1) for k, v in d.items()}
    r4 = lambda d: {k: round(v, 4) for k, v in d.items()}
    return {cy: {"by_region": {r: r1(v) for r, v in rec["by_region"].items()},
                 "by_group": {r: r1(v) for r, v in rec["by_group"].items()},
                 "beq": r1(rec["beq"]), "beq_gross": r1(rec["beq_gross"]), "coproducts": r1(rec["coproducts"]), "relative_price": rec["relative_price"], "availability": rec["availability"],
                 "base_mix": {r: r4(v) for r, v in rec["base_mix"].items()},
                 "prices": {r: {g: round(prices[r][g][cy], 1) for g in prices[r] if cy in prices[r][g]} for r in prices},
                 "drivers": rec["drivers"], "meta": rec["meta"], "corn_west": rec["corn_west"]}
            for cy, rec in out.items()}


# ------------------------------------------------------------------ main

def main() -> None:
    con = connect()
    years = [y for (y,) in con.execute("select distinct crop_year from gsw order by 1").fetchall()]
    cur = years[-1]
    sd, cornd, prod, pr, farm = supply_disposition(), corn(), production(), prices(), farm_feed()
    all_years = sorted(set(years) | {cy for g in sd.values() for cy in g})
    all_years = [y for y in all_years if y >= "2012-2013"]
    regional = regional_feed(sd, farm, cornd, prod, all_years)
    panel = model_panel(sd, cornd, pr, regional, all_years)
    # the grain mix: share ~ price ^ -PRICE_ELASTICITY x availability ^ a (a from the farm survey)
    fit = fit_elasticities(panel, farm, cur)
    sigma, alpha = -fit["b"], fit["a"]
    durum, durum_years = durum_share(farm)
    avail, supply = availability(sd, prod, all_years + [cur], cur)
    cop, processed = coproducts(sd, cornd, all_years + [cur])
    corn_w = western_corn(cornd, prod, all_years)
    # forage: hay shortfall needs the model's beef cow numbers, so run once without it, then rerun
    base = model_demand(pr, all_years + [cur], corn_w, durum, avail, sigma, alpha, cop)
    short, hay = hay_shortfall(base, sorted(base))
    forage = {"west": {cy: HAY_GRAIN_RATE * v for cy, v in short.items()}}
    demand = model_demand(pr, all_years + [cur], corn_w, durum, avail, sigma, alpha, cop, forage)
    origin = corn_origin(demand, cornd, prod)
    for cy, o in origin.items():
        for r in REGIONS:
            demand[cy]["by_region"][r]["corn_ca"] = o[r]["ca"]
            demand[cy]["by_region"][r]["corn_us"] = o[r]["us"]
        demand[cy]["corn_origin"] = o

    data = {
        "meta": {"current_year": cur, "generated": dt.date.today().isoformat(),
                 "assumptions": {"energy_vs_barley": ENERGY, "corn_basis_usd_bu": CORN_BASIS_USD_BU, "bu_corn_per_t": BU_CORN_PER_T}},
        "regional_feed": regional,
        "prices": pr,
        "demand_model": demand,
        "demand_model_meta": {"west_cattle_mix": feed_model.WEST_CATTLE_MIX,
                              "feedlot_lb_day": feed_model.FEEDLOT_BARLEY_LB_DAY, "background_lb_day": feed_model.BACKGROUND_BARLEY_LB_DAY,
                              "background_days": feed_model.BACKGROUND_DAYS, "hog_factors": feed_model.HOG_FACTORS,
                              "durum_share_west": round(durum, 4), "durum_share_years": durum_years,
                              "sigma": round(sigma, 3), "alpha": round(alpha, 3), "fed_share": feed_model.FED_SHARE,
                              "energy": feed_model.ENERGY, "supply": supply,
                              "coproduct_yield": COPRODUCT_YIELD, "coproduct_energy": COPRODUCT_ENERGY, "processed": processed,
                              "hay_grain_rate": round(HAY_GRAIN_RATE, 4), "hay": hay, "price_elasticity": PRICE_ELASTICITY,
                              "alpha_se": round(fit["a_se"], 3), "corn_west_measured": {cy: round(v, 1) for cy, v in corn_w.items()}},
        "model": {"fit": fit},
        "panel": panel.round(1).reset_index().to_dict(orient="records"),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(clean_json(data), separators=(",", ":"), allow_nan=False)
    OUT_PATH.write_text(text)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)} ({len(text) / 1e3:.0f} KB)")
    print(f"elasticities: price {-sigma:.3f}, availability {alpha:.3f} ({fit['train_years'][0]} to {fit['train_years'][1]})")
    print(f"availability elasticity (farm survey): {alpha:.2f} (se {fit['a_se']:.2f}, n {fit['n']}); hay shortfall:", {cy: hay[cy]['shortfall_kt'] for cy in list(hay)[-4:]})
    dm = demand[cur]
    for r in REGIONS:
        print(f"{cur} {r}:", {g: round(dm['by_region'][r][g]) for g in GRAINS + ['corn_ca', 'corn_us']})
    print("meta:", dm["meta"])


if __name__ == "__main__":
    main()
