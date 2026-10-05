"""Build dashboard/feed/data.json: Canadian feed grain use, supply, quality, livestock and prices,
with a West (MB, SK, AB, BC) and East (Ontario, Quebec and the Atlantic provinces) breakdown.

Run after src/ingest.py:
    python src/feed_data.py

Two measures of feed use:

1. The feed demand model (src/feed_model.py), the page's headline: grain fed, built from animal
   numbers x feeding rates, by province, livestock class and grain. See that module.

2. StatCan's "animal feed, waste and dockage", a residual of its supply and disposition balance
   (what is left after exports, processing, seed and stocks), so it also carries waste, dockage
   and balancing error. Published three times per crop year, cumulative: December (Aug-Dec),
   March (Aug-Mar) and July (full crop year; corn: August, Sep-Aug), for Canada only.
   This page splits it into regions:
    barley, wheat, durum, oats  each region's on-farm feed (32-10-0015, published by region)
                                plus a share of the rest of Canada's feed use (fed off the farm
                                that grew it) in proportion to the region's production
    corn                        West = domestic use in "other provinces" (32-10-0014, i.e. the West
                                and the Atlantic provinces) less seed and the Atlantic crop (assumed
                                fed where grown); East = the rest of Canada's corn feed. Corn for
                                industry (ethanol, starch) is almost all in Ontario and Quebec.
   The regions add up to StatCan's Canada total. The West's measured corn use also sets the
   demand model's western corn (the 1999 rations predate Manitoba's corn crop), and durum's
   median share of the West's wheat and durum feed sets the model's split of wheat into wheat
   and durum (the 1999 study doesn't separate them).

   A forecast of the residual for the current crop year (shown lower on the page):
    total   = average total feed use (barley, wheat, durum, oats, western corn) of the last five crop years
    shares  = split across those grains by a model fitted on past crop years:
              log(share_i / share_barley) = crop constant
                  + a * log(availability ratio) + b * log(energy-adjusted price ratio)
    estimate for each grain = 50% model + 50% its own five-year average
    eastern corn = its five-year average: it is fed from the local
              crop and doesn't trade off against western barley on price, so it stays out of the model
    regions = each small grain's estimate x the region's average share of it over the same five years
   Backtested year by year on earlier years only (like the canola forecast), against the
   five-year average and last year's use.
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
WEEKS = 52
WEST = ["Manitoba", "Saskatchewan", "Alberta", "British Columbia"]
ATLANTIC = ["New Brunswick", "Nova Scotia", "Prince Edward Island", "Newfoundland and Labrador"]
EAST = ["Ontario", "Quebec"] + ATLANTIC
REGIONS = ["west", "east"]

# StatCan supply-and-disposition crop names -> page keys
SD_CROPS = {"Barley": "barley", "Wheat, excluding durum": "wheat", "Durum wheat": "durum", "Oats": "oats"}
SMALL = ["barley", "wheat", "durum", "oats"]
GRAINS = SMALL + ["corn"]
MODEL_GRAINS = SMALL + ["corn_west"]  # grains in the share model; eastern corn is estimated separately
PROD_NAMES = {"barley": "Barley", "wheat": "Wheat, all excluding durum wheat", "durum": "Wheat, durum", "oats": "Oats",
              "corn": "Corn for grain"}

# Alberta monthly farm prices (C$/t): the feed price of each small grain, plus cattle and hogs
PRICE_SERIES = {
    "barley": "Barley for animal feed",
    "wheat": "Wheat (except durum wheat), other",
    "durum": "Durum wheat",
    "oats": "Oats",
    "malt_barley": "Barley for malt and other human consumption",
    "milling_wheat": "Wheat (except durum wheat), milling",
    "steers_slaughter": "Steers for slaughter",
    "steers_feeding": "Steers for feeding",
    "hogs": "Hogs",
}
# Ontario monthly farm prices (C$/t), the Eastern benchmark. Ontario corn is the corn price in the model.
EAST_PRICE_SERIES = {"corn": "Corn for grain", "barley": "Barley", "wheat": "Ontario wheat excluding payments", "oats": "Oats"}
# Feeding value relative to barley (cattle energy basis). Editable on the page.
ENERGY = {"barley": 1.00, "wheat": 1.08, "durum": 1.06, "oats": 0.85, "corn": 1.12}
# US corn delivered to southern Alberta = CBOT + this basis and freight (US$/bu). Editable on the page.
CORN_BASIS_USD_BU = 1.60
BU_CORN_PER_T = 39.368

CANOLA_MEAL_YIELD = 0.57  # tonnes of meal per tonne of canola crushed


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
        ("Ontario", "Imports from other countries"): "imports_on",
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


def livestock() -> dict:
    """Inventories (Jan 1 / Jul 1) for the West and the East, Canada slaughter
    and meat production, and poultry meat by region."""
    cat = external.statcan("32100130")
    hogs = external.statcan("32100160")
    hogs = hogs[hogs["Livestock"] == "Hogs, total"]
    key = lambda r, s: f"{r}-{'01' if s.startswith('At January') else '07'}"

    def region_sum(t, geos):
        g = t.groupby(["REF_DATE", "Survey date"])["VALUE"].agg(lambda v: v.sum() if v.notna().all() and len(v) == len(geos) else np.nan)
        return {key(r, s): round(float(v), 1) for (r, s), v in g.items() if not pd.isna(v) and int(r) >= 2010}

    inv: dict = {}
    for region, geos in (("west", ["Western provinces"]), ("east", ["Eastern provinces"])):
        c = cat[cat["GEO"].isin(geos)]
        cattle = lambda livestock, farm_type: region_sum(c[(c["Livestock"] == livestock) & (c["Farm type"] == farm_type)], geos)
        inv[region] = {
            "total_cattle": cattle("Total cattle", "On all cattle operations"),
            "beef_cows": cattle("Beef cows", "On all cattle operations"),
            "dairy_cows": cattle("Dairy cows", "On all cattle operations"),
            "feedlot_steers": cattle("Steers, 1 year and over", "On feeding operations"),
            "feedlot_heifers": cattle("Heifers for slaughter", "On feeding operations"),
            "feedlot_calves": cattle("Calves, under 1 year", "On feeding operations"),
            "hogs": region_sum(hogs[hogs["GEO"].isin(geos)], geos),
        }

    def annual(table, livestock, estimate, scale=1.0):
        t = external.statcan(table)
        g = t[(t["Livestock"] == livestock) & (t["Livestock estimates"] == estimate) & (t["GEO"] == "Canada")]
        return {str(r): round(float(v) * scale, 1) for r, v in zip(g["REF_DATE"], g["VALUE"]) if not pd.isna(v) and int(r) >= 2005}
    slaughter = {
        "cattle": annual("32100125", "Cattle", "Total slaughter"),
        "calves": annual("32100125", "Calves", "Total slaughter"),
        "hogs": annual("32100126", "Hogs", "Total slaughter"),
    }
    # meat production: StatCan reports tonnes; convert to kt
    meat = {
        "beef": annual("32100125", "Cattle", "Estimated meat production", 1 / 1000),
        "veal": annual("32100125", "Calves", "Estimated meat production", 1 / 1000),
        "pork": annual("32100126", "Hogs", "Estimated meat production", 1 / 1000),
    }
    poul = external.statcan("32100117")
    poul = poul[(poul["Commodity"] == "Total poultry") & (poul["Production and disposition"] == "Production, total")
                & (poul["Estimates"] == "Weight (kilograms)")]
    # poultry weight is in thousands of kg (= tonnes); convert to kt
    canada = poul[poul["GEO"] == "Canada"]
    meat["poultry"] = {str(r): round(float(v) / 1000, 1) for r, v in zip(canada["REF_DATE"], canada["VALUE"]) if int(r) >= 2005}
    for region, geos in (("west", WEST), ("east", EAST)):
        s = poul[poul["GEO"].isin(geos)].groupby("REF_DATE")["VALUE"].sum() / 1000
        meat[f"poultry_{region}"] = {str(r): round(float(v), 1) for r, v in s.items() if int(r) >= 2005}
    return {"inventory": inv, "slaughter": slaughter, "meat": meat,
            "units": {"inventory": "thousand head", "slaughter": "thousand head", "meat": "kt"}}


# ------------------------------------------------------------------ CGC weekly (Western Canada)

def cgc_weekly(con, years: list[str]) -> dict:
    """CGC weekly crop-year-to-date series (kt) for the feed page's flash table, Western Canada only.
    Gaps inside a year are carried forward."""
    def series(sql):
        rows = con.execute(sql).fetchall()
        out: dict = {}
        for key, y, w, kt in rows:
            out.setdefault(key, {}).setdefault(y, [None] * WEEKS)[w - 1] = round(kt, 1)
        for s in out.values():
            for arr in s.values():
                last = max(i for i, v in enumerate(arr) if v is not None)
                for i in range(1, last + 1):
                    if arr[i] is None:
                        arr[i] = arr[i - 1]
        return out
    return series("""
        select lower(grain) || '_feed_deliveries', crop_year, grain_week, sum(ktonnes) from gsw
        where worksheet = 'Feed Grains' and metric = 'Deliveries' and period = 'Crop Year' and grain in ('Wheat', 'Barley') group by all
        union all
        select lower(grain) || '_feed_shipments', crop_year, grain_week, sum(ktonnes) from gsw
        where worksheet = 'Feed Grains' and metric = 'Shipments' and period = 'Crop Year' and grain in ('Wheat', 'Barley') group by all
        union all
        select lower(grain) || '_primary_deliveries', crop_year, grain_week, sum(ktonnes) from gsw
        where worksheet = 'Primary' and metric = 'Deliveries' and period = 'Crop Year' and grain in ('Wheat', 'Barley') group by all
        union all
        select 'us_corn_receipts', crop_year, grain_week, sum(ktonnes) from gsw
        where worksheet = 'Imported Grains' and metric = 'Receipts' and period = 'Crop Year' and grain = 'U.S. Corn'
          and region in ('Primary Elevators', 'Process Elevators') group by all
        union all
        select 'canola_crush', crop_year, grain_week, sum(ktonnes) from gsw
        where grain = 'Canola' and worksheet = 'Process' and metric = 'Milled/Mfg Grain' and period = 'Crop Year' group by all
    """)


# ------------------------------------------------------------------ StatCan residual estimate

def crop_year_price(series: dict, cy: str) -> float | None:
    """Average of the monthly prices in an Aug-Jul crop year (None if no months are published)."""
    y0 = int(cy[:4])
    months = [f"{y0}-{m:02d}" for m in range(8, 13)] + [f"{y0 + 1}-{m:02d}" for m in range(1, 8)]
    v = [series[m] for m in months if m in series]
    return float(np.mean(v)) if v else None


def model_panel(sd, cornd, pr, regional, years) -> pd.DataFrame:
    """One row per crop year. Model grains: barley, wheat, durum and oats (Canada) and corn fed in
    the West; eastern corn is kept separate."""
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


def fit_shares(panel: pd.DataFrame, train: list[str]):
    """Pooled least squares: log(s_i/s_b) = c_i + a log(avail_i/avail_b) + b log(price_i/price_b).
    Availability is supply relative to the grain's own mean over the training years (western
    corn: 1, freely available through imports)."""
    others = ["wheat", "durum", "oats", "corn_west"]
    means = {g: panel.loc[train, f"supply_{g}"].mean() for g in SMALL}
    X, y = [], []
    for cy in train:
        r = panel.loc[cy]
        for i, g in enumerate(others):
            a_i = r[f"supply_{g}"] / means[g] if g != "corn_west" else 1.0
            a_b = r["supply_barley"] / means["barley"]
            p_i = r[f"price_{g}"] / ENERGY[g.removesuffix("_west")]
            p_b = r["price_barley"] / ENERGY["barley"]
            if min(r[f"feed_{g}"], r["feed_barley"]) <= 0:
                continue
            dummies = [1.0 if j == i else 0.0 for j in range(len(others))]
            X.append(dummies + [np.log(a_i / a_b), np.log(p_i / p_b)])
            y.append(np.log(r[f"feed_{g}"] / r["feed_barley"]))
    X, y = np.array(X), np.array(y)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return {"const": dict(zip(others, coef[:4])), "a": float(coef[4]), "b": float(coef[5]), "means": means}


def predict_shares(fit, supply: dict, price: dict) -> dict:
    """Each model grain's share of total feed use, from the fitted model, supplies and prices."""
    a_b = supply["barley"] / fit["means"]["barley"]
    p_b = price["barley"] / ENERGY["barley"]
    rel = {"barley": 1.0}
    for g in ["wheat", "durum", "oats", "corn_west"]:
        a_i = supply[g] / fit["means"][g] if g != "corn_west" else 1.0
        rel[g] = float(np.exp(fit["const"][g] + fit["a"] * np.log(a_i / a_b) + fit["b"] * np.log((price[g] / ENERGY[g.removesuffix("_west")]) / p_b)))
    tot = sum(rel.values())
    return {g: v / tot for g, v in rel.items()}


def blend(shares: dict, total: float, avg: dict) -> dict:
    """Estimate per model grain: half the model's share of the total, half the grain's 5-yr average."""
    return {g: 0.5 * shares[g] * total + 0.5 * avg[g] for g in MODEL_GRAINS}


def region_shares(regional: dict, yrs: list[str]) -> dict:
    """Each region's average share of each small grain's Canada feed use over the given crop years."""
    out = {}
    for g in SMALL:
        rows = [regional[y][g] for y in yrs if g in regional.get(y, {})]
        out[g] = {k: float(np.mean([r[k] / sum(r[j] for j in REGIONS) for r in rows])) for k in REGIONS}
    return out


def by_region(est: dict, corn_east: float, shares: dict) -> dict:
    """Split a Canada estimate (model grains) plus eastern corn into regions."""
    out = {r: {g: est[g] * shares[g][r] for g in SMALL} for r in REGIONS}
    out["west"]["corn"] = est["corn_west"]
    out["east"]["corn"] = corn_east
    return out


def run_model(panel: pd.DataFrame, regional: dict, cur: str) -> dict:
    """Backtest the estimate year by year on earlier years only, against the 5-yr average and last
    year (by grain, Canada total and regional totals), then fit the model on every complete year."""
    total = lambda cy: sum(panel.loc[cy, f"feed_{g}"] for g in MODEL_GRAINS)
    region_total = lambda cy, r: sum(regional[cy][g][r] for g in GRAINS)
    need = [f"feed_{g}" for g in MODEL_GRAINS + ["corn_east"]] + [f"price_{g}" for g in MODEL_GRAINS] + [f"supply_{g}" for g in SMALL]
    done = [cy for cy in panel.index if cy != cur and panel.loc[cy, need].notna().all()]
    back = []
    for i, cy in enumerate(done):
        train = done[:i]
        if len(train) < 6:
            continue
        fit = fit_shares(panel, train)
        sh = predict_shares(fit, {g: panel.loc[cy, f"supply_{g}"] for g in SMALL}, {g: panel.loc[cy, f"price_{g}"] for g in MODEL_GRAINS})
        T = np.mean([total(t) for t in train[-5:]])
        avg = {g: np.mean([panel.loc[t, f"feed_{g}"] for t in train[-5:]]) for g in MODEL_GRAINS + ["corn_east"]}
        est, model_only = blend(sh, T, avg), {g: sh[g] * T for g in MODEL_GRAINS}
        last = {g: panel.loc[train[-1], f"feed_{g}"] for g in MODEL_GRAINS + ["corn_east"]}
        row = lambda g, actual, e, mo, a5, ly: {"cy": cy, "grain": g, "actual": actual, "estimate": e, "model_only": mo, "avg_5yr": a5, "last_year": ly}
        for g in MODEL_GRAINS:
            back.append(row(g, panel.loc[cy, f"feed_{g}"], est[g], model_only[g], avg[g], last[g]))
        # eastern corn is its own 5-yr average in every method but last year's
        ce = avg["corn_east"]
        back.append(row("corn_east", panel.loc[cy, "feed_corn_east"], ce, ce, ce, last["corn_east"]))
        back.append(row("corn", panel.loc[cy, "feed_corn_west"] + panel.loc[cy, "feed_corn_east"], est["corn_west"] + ce, model_only["corn_west"] + ce,
                        avg["corn_west"] + ce, last["corn_west"] + last["corn_east"]))
        back.append(row("total", total(cy) + panel.loc[cy, "feed_corn_east"], T + ce, T + ce, T + ce, total(train[-1]) + last["corn_east"]))
        # regional totals: each method's estimate split by the regions' 5-yr average shares
        if all(t in regional and all(g in regional[t] for g in GRAINS) for t in train[-5:] + [cy]):
            rs = region_shares(regional, train[-5:])
            split = {m: by_region(v, ce, rs) for m, v in (("estimate", est), ("model_only", model_only))}
            for r in ("west", "east"):
                back.append(row(r, region_total(cy, r), sum(split["estimate"][r].values()), sum(split["model_only"][r].values()),
                                np.mean([region_total(t, r) for t in train[-5:]]), region_total(train[-1], r)))
    bt = pd.DataFrame(back)
    methods = ["estimate", "model_only", "avg_5yr", "last_year"]
    err = {g: {m: float(np.mean(np.abs(grp[m] - grp["actual"]))) for m in methods} for g, grp in bt.groupby("grain")}
    fit = fit_shares(panel, done)
    return {"fit": {"a": fit["a"], "b": fit["b"], "const": fit["const"], "train_years": [done[0], done[-1]]},
            "test_years": sorted(bt["cy"].unique().tolist()), "backtest_mae": err,
            "backtest": bt.round(1).to_dict(orient="records"), "fitted": fit, "done": done}


def current_estimate(model: dict, panel: pd.DataFrame, sd: dict, cornd: dict, prod: dict, pr: dict, regional: dict, cur: str) -> dict:
    """Current crop year: supply = carry-in (last July's ending stocks) + StatCan's latest
    production estimate + last year's imports; prices = the latest month (western corn: this
    week's CBOT close delivered to Alberta). Eastern corn = its 5-year average."""
    last = f"{int(cur[:4]) - 1}-{cur[:4]}"
    supply, basis = {}, {}
    for g in SMALL:
        carry = sd[g][last]["jul"]["carry_out"]
        prodn = prod[g]["canada"].get(cur[:4])
        imports = sd[g][last]["jul"].get("imports", 0)
        supply[g] = carry + (prodn or 0) + imports
        basis[g] = {"carry_in": carry, "production": prodn, "imports": imports}
    latest = {g: list(pr[g]["values"].items())[-1] for g in SMALL}
    lm = pr["latest_market"]
    price = {g: latest[g][1] for g in SMALL} | {"corn_west": corn_delivered(lm["cbot_corn"], lm["usdcad"])}
    fit = model["fitted"]
    shares = predict_shares(fit, supply, price)
    recent = model["done"][-5:]
    total = float(np.mean([sum(panel.loc[t, f"feed_{g}"] for g in MODEL_GRAINS) for t in recent]))
    avg = {g: float(np.mean([panel.loc[t, f"feed_{g}"] for t in recent])) for g in MODEL_GRAINS + ["corn_east"]}
    est = blend(shares, total, avg)
    model_only = {g: shares[g] * total for g in MODEL_GRAINS}
    corn_east = avg["corn_east"]
    rs = region_shares(regional, recent)
    regions = by_region(est, corn_east, rs)
    canada = lambda d: {g: d[g] for g in SMALL} | {"corn": d["corn_west"] + corn_east}
    r0 = lambda d: {g: round(v, 0) for g, v in d.items()}
    ly = lambda g: None if pd.isna(panel.loc[last, f"feed_{g}"]) else round(float(panel.loc[last, f"feed_{g}"]), 0)
    return {
        "crop_year": cur, "total": round(sum(est.values()) + corn_east, 0), "avg_years": [recent[0], recent[-1]],
        "by_grain": r0(canada(est)), "corn_west": round(est["corn_west"], 0), "corn_east": round(corn_east, 0),
        "by_region": {r: r0(v) for r, v in regions.items()},
        "region_shares": {g: {r: round(v, 4) for r, v in s.items()} for g, s in rs.items()},
        "model_only": r0(canada(model_only)),
        "avg_5yr": r0(canada(avg)),
        "avg_5yr_by_region": {r: {g: round(float(np.mean([regional[t][g][r] for t in recent])), 0) for g in GRAINS} for r in REGIONS},
        "shares": {g: round(shares[g], 4) for g in MODEL_GRAINS},
        "supply": r0(supply), "supply_basis": basis,
        "prices": {g: round(v, 1) for g, v in price.items()},
        "price_months": {g: latest[g][0] for g in SMALL} | {"corn_west": lm["date"]},
        "last_year": {g: ly(g) for g in SMALL} | {"corn": ly("corn_west") + ly("corn_east")},
        "last_year_corn_west": ly("corn_west"), "last_year_corn_east": ly("corn_east"),
        "last_year_by_region": {r: {g: regional.get(last, {}).get(g, {}).get(r) for g in GRAINS} for r in REGIONS},
        "last_year_corn_estimated": bool(panel.loc[last, "corn_estimated"]),
    }


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

def durum_share(regional: dict) -> tuple[float, list[str]]:
    """Durum's median share (tonnes) of the wheat and durum fed in the West, over the crop years
    StatCan has published; the median keeps out quality years like 2016-17, when a wet harvest
    sent 2.1 Mt of durum to feed. Returns (share, [first, last crop year])."""
    yrs = sorted(cy for cy, r in regional.items() if "wheat" in r and "durum" in r and r["wheat"]["west"] + r["durum"]["west"] > 0)
    shares = [regional[cy]["durum"]["west"] / (regional[cy]["wheat"]["west"] + regional[cy]["durum"]["west"]) for cy in yrs]
    return float(np.median(shares)), [yrs[0], yrs[-1]]


def model_demand(pr: dict, years: list[str], corn_west: dict, durum: float) -> dict:
    """Feed demand from animal numbers (src/feed_model.py), priced off each region's grains:
    West = Alberta farm prices and US corn delivered to southern Alberta; East = Ontario.
    durum: durum's base share of the West's wheat and durum (see durum_share)."""
    years = sorted(set(years))
    us = {}
    for cy in years:
        zc, fx = crop_year_price(pr["cbot_corn"]["values"], cy), crop_year_price(pr["usdcad"]["values"], cy)
        if zc and fx:
            us[cy] = corn_delivered(zc, fx)
    series = {"west": {g: pr[g]["values"] for g in ("barley", "wheat", "durum", "oats")},
              "east": {g: pr["ontario"][g]["values"] for g in ("corn", "barley", "wheat", "oats")}}
    prices = {r: {g: {cy: v for cy in years if (v := crop_year_price(s, cy))} for g, s in gs.items()} for r, gs in series.items()}
    prices["west"]["corn"] = us
    out = feed_model.demand([int(cy[:4]) for cy in years], prices, corn_west, durum)
    r1 = lambda d: {k: round(v, 1) for k, v in d.items()}
    return {cy: {"by_region": {r: r1(v) for r, v in rec["by_region"].items()},
                 "by_group": {r: r1(v) for r, v in rec["by_group"].items()},
                 "beq": r1(rec["beq"]), "relative_price": rec["relative_price"], "drivers": rec["drivers"], "meta": rec["meta"],
                 "corn_west": rec["corn_west"]}
            for cy, rec in out.items()}


# ------------------------------------------------------------------ main

def main() -> None:
    con = connect()
    years = [y for (y,) in con.execute("select distinct crop_year from gsw order by 1").fetchall()]
    cur = years[-1]
    latest_week, week_ending = con.execute(
        "select grain_week, week_ending from gsw where crop_year = ? order by grain_week desc limit 1", [cur]).fetchone()

    sd, cornd, prod, pr, liv, farm = supply_disposition(), corn(), production(), prices(), livestock(), farm_feed()
    all_years = sorted(set(years) | {cy for g in sd.values() for cy in g})
    all_years = [y for y in all_years if y >= "2012-2013"]
    regional = regional_feed(sd, farm, cornd, prod, all_years)
    durum, durum_years = durum_share(regional)
    demand = model_demand(pr, all_years + [cur], {cy: r["corn"]["west"] for cy, r in regional.items() if "corn" in r and not r["corn"]["estimated"]},
                          durum)
    panel = model_panel(sd, cornd, pr, regional, all_years)
    model = run_model(panel, regional, cur)
    estimate = current_estimate(model, panel, sd, cornd, prod, pr, regional, cur)

    data = {
        "meta": {"current_year": cur, "latest_week": latest_week, "week_ending": week_ending.date().isoformat(),
                 "generated": dt.date.today().isoformat(),
                 "assumptions": {"energy_vs_barley": ENERGY, "corn_basis_usd_bu": CORN_BASIS_USD_BU,
                                 "bu_corn_per_t": BU_CORN_PER_T,
                                 "canola_meal_yield": CANOLA_MEAL_YIELD}},
        "years": years,
        "supply_disposition": sd,
        "farm_feed": farm,
        "regional_feed": regional,
        "corn": cornd,
        "production": prod,
        "prices": pr,
        "livestock": liv,
        "demand_model": demand,
        "demand_model_meta": {"sigma": feed_model.SIGMA, "west_cattle_mix": feed_model.WEST_CATTLE_MIX,
                              "feedlot_lb_day": feed_model.FEEDLOT_BARLEY_LB_DAY, "background_lb_day": feed_model.BACKGROUND_BARLEY_LB_DAY,
                              "background_days": feed_model.BACKGROUND_DAYS, "hog_factors": feed_model.HOG_FACTORS,
                              "durum_share_west": round(durum, 4), "durum_share_years": durum_years},
        "weekly": cgc_weekly(con, years),
        "model": {k: v for k, v in model.items() if k not in ("fitted", "done")},
        "panel": panel.round(1).reset_index().to_dict(orient="records"),
        "estimate": estimate,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(clean_json(data), separators=(",", ":"), allow_nan=False)
    OUT_PATH.write_text(text)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)} ({len(text) / 1e3:.0f} KB)")
    print("model:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in model["fit"].items() if k != "const"}, "test years", model["test_years"])
    print("backtest MAE (kt):", {g: {m: round(e) for m, e in v.items()} for g, v in model["backtest_mae"].items()})
    print("estimate", cur, ":", estimate["total"], estimate["by_grain"])
    print("by region:", {r: round(sum(v.values())) for r, v in estimate["by_region"].items()})
    dm = demand[cur]
    print("demand model", cur, ":", {r: round(sum(v.values())) for r, v in dm["by_region"].items()}, dm["meta"])
    print("last year:", estimate["last_year"], "(corn estimated)" if estimate["last_year_corn_estimated"] else "")
    print("supply:", estimate["supply"], "prices:", estimate["prices"], estimate["price_months"])


if __name__ == "__main__":
    main()
