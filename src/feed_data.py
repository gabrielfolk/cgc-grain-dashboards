"""Build dashboard/feed/data.json: Western Canadian feed grain use, supply, quality, livestock and prices.

Run after src/ingest.py:
    python src/feed_data.py

Feed use (StatCan "animal feed, waste and dockage") is estimated by StatCan as a residual of
its supply and disposition balance. It is published three times per crop year, cumulative:
December (Aug-Dec), March (Aug-Mar) and July (full crop year).

The page's estimate for the current crop year:
    total   = average total feed use of the last five crop years
    shares  = split across barley, wheat, durum, oats and imported corn by a model fitted on
              past crop years: log(share_i / share_barley) = crop constant
                  + a * log(availability ratio) + b * log(energy-adjusted price ratio)
    estimate for each grain = 50% model + 50% its own five-year average
Backtested year by year on earlier years only (like the canola forecast), the 50/50 blend
beat the five-year average, last year's use, and the model alone; scaling the total by
the livestock demand index made it worse, so the index is shown as context only.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

import external
from db import connect

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "dashboard" / "feed" / "data.json"
WEEKS = 52
WEST = ["Manitoba", "Saskatchewan", "Alberta", "British Columbia"]

# StatCan supply-and-disposition crop names -> page keys
SD_CROPS = {"Barley": "barley", "Wheat, excluding durum": "wheat", "Durum wheat": "durum", "Oats": "oats"}
GRAINS = ["barley", "wheat", "durum", "oats", "corn"]
PROD_NAMES = {"barley": "Barley", "wheat": "Wheat, all excluding durum wheat", "durum": "Wheat, durum", "oats": "Oats"}

# Alberta monthly farm prices (C$/t) used as the feed price of each grain
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
# Feeding value relative to barley (cattle energy basis). Editable on the page.
ENERGY = {"barley": 1.00, "wheat": 1.08, "durum": 1.06, "oats": 0.85, "corn": 1.12}
# US corn delivered to southern Alberta = CBOT + this basis and freight (US$/bu). Editable on the page.
CORN_BASIS_USD_BU = 1.60
BU_CORN_PER_T = 39.368

# Grain-consuming demand weights: tonnes of grain per head of inventory per year
# (feedlot cattle, cows, hogs) and per tonne of poultry meat produced.
DEMAND_WEIGHTS = {"feedlot": 2.7, "beef_cows": 0.15, "dairy_cows": 1.5, "hogs": 0.55, "poultry": 1.6}
CANOLA_MEAL_YIELD = 0.57  # tonnes of meal per tonne of canola crushed


def crop_year_of(date: pd.Timestamp) -> str:
    y = date.year if date.month >= 8 else date.year - 1
    return f"{y}-{y + 1}"


# ------------------------------------------------------------------ StatCan

def supply_disposition() -> dict:
    """Crop-year totals (July, cumulative) and in-year partials (Dec, Mar) by crop, kt."""
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


def western_farm_feed() -> dict:
    fs = external.statcan("32100015")
    fs = fs[(fs["GEO"] == "Western Canada") & fs["Type of crop"].isin(SD_CROPS)
            & (fs["Farm supply and disposition of grains"] == "Animal feed, waste and dockage")]
    out: dict = {}
    for crop, g in fs.groupby("Type of crop"):
        for ref, v in zip(g["REF_DATE"], g["VALUE"]):
            date = pd.Timestamp(ref + "-01")
            if date.month == 7 and not pd.isna(v):
                out.setdefault(SD_CROPS[crop], {})[crop_year_of(date)] = round(float(v), 1)
    return out


def corn() -> dict:
    """Corn (crop year Sep-Aug, cumulative at August): imports into provinces other than
    Ontario and Quebec (mostly Western Canada), and Canada feed use."""
    c = external.statcan("32100014")
    out: dict = {}
    for (geo, item), g in c.groupby(["GEO", "Supply and disposition of corn"]):
        key = {("Other provinces", "Imports from other countries"): "imports_west",
               ("Canada", "Imports from other countries"): "imports_canada",
               ("Canada", "Animal feed, waste and dockage"): "feed_canada",
               ("Canada", "Production"): "production_canada"}.get((geo, item))
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


def production() -> dict:
    wp = external.western_production()
    return {k: {str(y): round(v / 1000, 1) for y, v in wp[name].dropna().items() if y >= 2010}
            for k, name in PROD_NAMES.items() if name in wp.columns}


def prices() -> dict:
    """Monthly Alberta farm prices (C$/t) and delivered US corn (C$/t, CBOT + basis/freight)."""
    p = external.statcan("32100077")
    p = p[p["GEO"] == "Alberta"]
    out: dict = {}
    for key, name in PRICE_SERIES.items():
        g = p[p["Farm products"].str.startswith(name + " [") | (p["Farm products"] == name)]
        uom = g["UOM"].iloc[0] if len(g) else ""
        out[key] = {"uom": uom, "values": {r: round(float(v), 2) for r, v in zip(g["REF_DATE"], g["VALUE"]) if not pd.isna(v) and r >= "2012-01"}}
    zc = external.yahoo_weekly("ZC=F")
    fx = external.yahoo_weekly("CAD=X")
    m = pd.DataFrame({"zc": zc, "fx": fx}).sort_index().ffill().dropna()
    monthly = m.resample("MS").mean()
    out["cbot_corn"] = {"uom": "US cents per bushel", "values": {d.strftime("%Y-%m"): round(v, 2) for d, v in monthly["zc"].items()}}
    out["usdcad"] = {"uom": "CAD per USD", "values": {d.strftime("%Y-%m"): round(v, 4) for d, v in monthly["fx"].items()}}
    last = m.iloc[-1]
    out["latest_market"] = {"date": m.index[-1].date().isoformat(), "cbot_corn": round(float(last["zc"]), 2), "usdcad": round(float(last["fx"]), 4)}
    return out


def corn_delivered(zc_cents: float, fx: float, basis_usd_bu: float = CORN_BASIS_USD_BU) -> float:
    return (zc_cents / 100 + basis_usd_bu) * BU_CORN_PER_T * fx


def livestock() -> dict:
    """Western Canada inventories (Jan 1 / Jul 1), Canada slaughter and meat production."""
    cat = external.statcan("32100130")
    cat = cat[cat["GEO"] == "Western provinces"]
    def cattle(livestock, farm_type):
        g = cat[(cat["Livestock"] == livestock) & (cat["Farm type"] == farm_type)]
        return {f"{r}-{'01' if s.startswith('At January') else '07'}": round(float(v), 1)
                for r, s, v in zip(g["REF_DATE"], g["Survey date"], g["VALUE"]) if not pd.isna(v) and int(r) >= 2010}
    inv = {
        "total_cattle": cattle("Total cattle", "On all cattle operations"),
        "beef_cows": cattle("Beef cows", "On all cattle operations"),
        "dairy_cows": cattle("Dairy cows", "On all cattle operations"),
        "feedlot_steers": cattle("Steers, 1 year and over", "On feeding operations"),
        "feedlot_heifers": cattle("Heifers for slaughter", "On feeding operations"),
        "feedlot_calves": cattle("Calves, under 1 year", "On feeding operations"),
    }
    hogs = external.statcan("32100160")
    hogs = hogs[(hogs["GEO"] == "Western provinces") & (hogs["Livestock"] == "Hogs, total")]
    inv["hogs"] = {f"{r}-{'01' if s.startswith('At January') else '07'}": round(float(v), 1)
                   for r, s, v in zip(hogs["REF_DATE"], hogs["Survey date"], hogs["VALUE"]) if not pd.isna(v)}

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
    meat["poultry"] = {str(r): round(float(v) / 1000, 1) for r, v in zip(poul[poul["GEO"] == "Canada"]["REF_DATE"], poul[poul["GEO"] == "Canada"]["VALUE"]) if int(r) >= 2005}
    west_poultry = poul[poul["GEO"].isin(WEST)].groupby("REF_DATE")["VALUE"].sum() / 1000
    meat["poultry_west"] = {str(r): round(float(v), 1) for r, v in west_poultry.items() if int(r) >= 2005}
    return {"inventory": inv, "slaughter": slaughter, "meat": meat,
            "units": {"inventory": "thousand head", "slaughter": "thousand head", "meat": "kt"}}


# ------------------------------------------------------------------ CGC weekly

def cgc_weekly(con, years: list[str]) -> dict:
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


# ------------------------------------------------------------------ demand index + model

def demand_index(liv: dict, years: list[str]) -> dict:
    """Grain-consuming demand index (kt of grain per year) for each crop year, from the
    Jul 1 inventory at the start of the crop year and the Jan 1 inventory in it."""
    inv = liv["inventory"]
    out = {}
    for cy in years:
        y0, y1 = cy[:4], cy[5:]
        def avg(series):
            v = [inv[series].get(f"{y0}-07"), inv[series].get(f"{y1}-01")]
            v = [x for x in v if x is not None]
            return sum(v) / len(v) if v else None
        feedlot = [avg(k) for k in ("feedlot_steers", "feedlot_heifers")]
        parts = {
            "feedlot": sum(feedlot) if all(x is not None for x in feedlot) else None,
            "beef_cows": avg("beef_cows"), "dairy_cows": avg("dairy_cows"), "hogs": avg("hogs"),
            "poultry": liv["meat"]["poultry_west"].get(y0),
        }
        if any(v is None for v in parts.values()):
            continue
        out[cy] = {k: round(v * DEMAND_WEIGHTS[k], 1) for k, v in parts.items()}
        out[cy]["total"] = round(sum(out[cy].values()), 1)
    return out


def crop_year_price(series: dict, cy: str) -> float | None:
    y0 = int(cy[:4])
    months = [f"{y0}-{m:02d}" for m in range(8, 13)] + [f"{y0 + 1}-{m:02d}" for m in range(1, 8)]
    v = [series[m] for m in months if m in series]
    return float(np.mean(v)) if v else None


def model_panel(sd, cornd, prod, pr, years) -> pd.DataFrame:
    rows = []
    for cy in years:
        rec = {"cy": cy}
        for g in ["barley", "wheat", "durum", "oats"]:
            full = sd.get(g, {}).get(cy, {}).get("jul")
            rec[f"feed_{g}"] = full.get("feed") if full else None
            rec[f"supply_{g}"] = full.get("supply") if full else None
            rec[f"price_{g}"] = crop_year_price(pr[g]["values"], cy)
        c = cornd.get(cy, {}).get("aug")
        rec["feed_corn"] = c.get("imports_west") if c else None
        rec["corn_estimated"] = False
        if rec["feed_corn"] is None and cornd.get(cy, {}).get("mar", {}).get("imports_west") is not None:
            # August not published yet: scale March by the usual March-to-August ratio
            prior = [k for k in sorted(cornd) if k < cy and "aug" in cornd[k] and "mar" in cornd[k]][-5:]
            ratio = np.mean([cornd[k]["aug"]["imports_west"] / cornd[k]["mar"]["imports_west"] for k in prior if cornd[k]["mar"]["imports_west"]])
            rec["feed_corn"] = cornd[cy]["mar"]["imports_west"] * ratio
            rec["corn_estimated"] = True
        rec["supply_corn"] = None
        zc, fx = crop_year_price(pr["cbot_corn"]["values"], cy), crop_year_price(pr["usdcad"]["values"], cy)
        rec["price_corn"] = corn_delivered(zc, fx) if zc and fx else None
        rows.append(rec)
    return pd.DataFrame(rows).set_index("cy")


def fit_shares(panel: pd.DataFrame, train: list[str]):
    """Pooled least squares: log(s_i/s_b) = c_i + a log(avail_i/avail_b) + b log(price_i/price_b).
    Availability is supply relative to the crop's own mean over the training years (corn: 1)."""
    others = ["wheat", "durum", "oats", "corn"]
    means = {g: panel.loc[train, f"supply_{g}"].mean() for g in ["barley", "wheat", "durum", "oats"]}
    X, y = [], []
    for cy in train:
        r = panel.loc[cy]
        for i, g in enumerate(others):
            a_i = r[f"supply_{g}"] / means[g] if g != "corn" else 1.0
            a_b = r["supply_barley"] / means["barley"]
            p_i = r[f"price_{g}"] / ENERGY[g]
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
    a_b = supply["barley"] / fit["means"]["barley"]
    p_b = price["barley"] / ENERGY["barley"]
    rel = {"barley": 1.0}
    for g in ["wheat", "durum", "oats", "corn"]:
        a_i = supply[g] / fit["means"][g] if g != "corn" else 1.0
        rel[g] = float(np.exp(fit["const"][g] + fit["a"] * np.log(a_i / a_b) + fit["b"] * np.log((price[g] / ENERGY[g]) / p_b)))
    tot = sum(rel.values())
    return {g: v / tot for g, v in rel.items()}


def blend(shares: dict, total: float, avg: dict) -> dict:
    """Estimate per grain: half the model's share of the total, half the grain's 5-yr average."""
    return {g: 0.5 * shares[g] * total + 0.5 * avg[g] for g in GRAINS}


def run_model(panel: pd.DataFrame, cur: str) -> dict:
    total = lambda cy: sum(panel.loc[cy, f"feed_{g}"] for g in GRAINS)
    done = [cy for cy in panel.index if cy != cur and panel.loc[cy, [f"feed_{g}" for g in GRAINS] + [f"price_{g}" for g in GRAINS]].notna().all()]
    back = []
    for i, cy in enumerate(done):
        train = done[:i]
        if len(train) < 6:
            continue
        fit = fit_shares(panel, train)
        sh = predict_shares(fit, {g: panel.loc[cy, f"supply_{g}"] for g in ["barley", "wheat", "durum", "oats"]} | {"corn": None},
                            {g: panel.loc[cy, f"price_{g}"] for g in GRAINS})
        T = np.mean([total(t) for t in train[-5:]])
        avg = {g: np.mean([panel.loc[t, f"feed_{g}"] for t in train[-5:]]) for g in GRAINS}
        est = blend(sh, T, avg)
        for g in GRAINS:
            back.append({"cy": cy, "grain": g, "actual": panel.loc[cy, f"feed_{g}"], "estimate": est[g], "model_only": sh[g] * T,
                         "avg_5yr": avg[g], "last_year": panel.loc[train[-1], f"feed_{g}"]})
        back.append({"cy": cy, "grain": "total", "actual": total(cy), "estimate": T, "model_only": T, "avg_5yr": T, "last_year": total(train[-1])})
    bt = pd.DataFrame(back)
    methods = ["estimate", "model_only", "avg_5yr", "last_year"]
    err = {g: {m: float(np.mean(np.abs(grp[m] - grp["actual"]))) for m in methods} for g, grp in bt.groupby("grain")}
    fit = fit_shares(panel, done)
    return {"fit": {"a": fit["a"], "b": fit["b"], "const": fit["const"], "train_years": [done[0], done[-1]]},
            "test_years": sorted(bt["cy"].unique().tolist()), "backtest_mae": err,
            "backtest": bt.round(1).to_dict(orient="records"), "fitted": fit, "done": done}


def current_estimate(model: dict, panel: pd.DataFrame, sd: dict, prod: dict, pr: dict, cur: str) -> dict:
    """Current crop year: supply = carry-in (last July's ending stocks) + StatCan's latest
    production estimate (+ imports assumed at last year's level); prices = the latest month."""
    last = f"{int(cur[:4]) - 1}-{cur[:4]}"
    supply, basis = {}, {}
    for g in ["barley", "wheat", "durum", "oats"]:
        carry = sd[g][last]["jul"]["carry_out"]
        prodn = prod[g].get(cur[:4])
        imports = sd[g][last]["jul"].get("imports", 0)
        supply[g] = carry + (prodn or 0) + imports
        basis[g] = {"carry_in": carry, "production": prodn, "imports": imports}
    latest = {g: list(pr[g]["values"].items())[-1] for g in ["barley", "wheat", "durum", "oats"]}
    lm = pr["latest_market"]
    price = {g: latest[g][1] for g in latest} | {"corn": corn_delivered(lm["cbot_corn"], lm["usdcad"])}
    fit = model["fitted"]
    shares = predict_shares(fit, supply | {"corn": None}, price)
    recent = model["done"][-5:]
    total = float(np.mean([sum(panel.loc[t, f"feed_{g}"] for g in GRAINS) for t in recent]))
    avg = {g: float(np.mean([panel.loc[t, f"feed_{g}"] for t in recent])) for g in GRAINS}
    est = blend(shares, total, avg)
    return {
        "crop_year": cur, "total": round(sum(est.values()), 0), "avg_years": [recent[0], recent[-1]],
        "by_grain": {g: round(v, 0) for g, v in est.items()},
        "model_only": {g: round(shares[g] * total, 0) for g in GRAINS},
        "avg_5yr": {g: round(v, 0) for g, v in avg.items()},
        "shares": {g: round(shares[g], 4) for g in GRAINS},
        "supply": {g: round(v, 0) for g, v in supply.items()}, "supply_basis": basis,
        "prices": {g: round(v, 1) for g, v in price.items()},
        "price_months": {g: latest[g][0] for g in latest} | {"corn": lm["date"]},
        "last_year": {g: (None if pd.isna(panel.loc[last, f"feed_{g}"]) else round(float(panel.loc[last, f"feed_{g}"]), 0)) for g in GRAINS},
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


# ------------------------------------------------------------------ main

def main() -> None:
    con = connect()
    years = [y for (y,) in con.execute("select distinct crop_year from gsw order by 1").fetchall()]
    cur = years[-1]
    latest_week, week_ending = con.execute(
        "select grain_week, week_ending from gsw where crop_year = ? order by grain_week desc limit 1", [cur]).fetchone()

    sd, cornd, prod, pr, liv = supply_disposition(), corn(), production(), prices(), livestock()
    all_years = sorted(set(years) | {cy for g in sd.values() for cy in g})
    all_years = [y for y in all_years if y >= "2012-2013"]
    demand = demand_index(liv, all_years)
    panel = model_panel(sd, cornd, prod, pr, all_years)
    model = run_model(panel, cur)
    estimate = current_estimate(model, panel, sd, prod, pr, cur)

    data = {
        "meta": {"current_year": cur, "latest_week": latest_week, "week_ending": week_ending.date().isoformat(),
                 "generated": dt.date.today().isoformat(),
                 "assumptions": {"energy_vs_barley": ENERGY, "corn_basis_usd_bu": CORN_BASIS_USD_BU,
                                 "bu_corn_per_t": BU_CORN_PER_T, "demand_weights": DEMAND_WEIGHTS,
                                 "canola_meal_yield": CANOLA_MEAL_YIELD}},
        "years": years,
        "supply_disposition": sd,
        "western_farm_feed": western_farm_feed(),
        "corn": cornd,
        "production": prod,
        "prices": pr,
        "livestock": liv,
        "demand_index": demand,
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
    print("last year:", estimate["last_year"], "(corn estimated)" if estimate["last_year_corn_estimated"] else "")
    print("supply:", estimate["supply"], "prices:", estimate["prices"], estimate["price_months"])


if __name__ == "__main__":
    main()
