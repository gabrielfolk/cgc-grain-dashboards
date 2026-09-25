"""Forecast canola crush and exports: weekly (1-4 weeks ahead) and full crop-year totals.

Every model is scored with a rolling-origin backtest: for each test crop year it is
trained only on earlier crop years, then asked to forecast from every week of the
test year. Baselines get the same treatment, so the comparison is fair.

    python src/canola_forecast.py            # backtest + current forecast
Writes reports/canola_forecast.json and a copy for the Canola Pipeline page (dashboard/canola/forecast.json).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import external
from grain_data import flows_sql
from db import connect

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "reports" / "canola_forecast.json"
DASHBOARD_PATH = ROOT / "dashboard" / "canola" / "forecast.json"

WEEKS = 52
TARGETS = ["crush", "exports"]
HORIZONS = [1, 2, 3, 4]
TEST_YEARS = 8           # backtest the last 8 completed crop years
MAX_PRIOR = 5            # seasonal profiles use up to 5 prior crop years
EPS = 5.0                # kt/week floor inside logs

# Weekly model feature sets, compared in the backtest.
BASE_COLS = ["t", "h", "mom_1", "mom_13", "vs_profile", "season_step", "ytd_pace",
             "other_vs_profile", "deliv_vs_profile", "stock_country", "stock_process", "stock_terminal"]
SUPPLY_COLS = ["supply"]
PRICE_COLS = ["soyoil_ret13", "oil_share", "oil_share_chg13", "fx_ret4", "canola_vs_soy", "margin_vs_3y"]
FEATURE_SETS = {
    "cgc": BASE_COLS,
    "cgc+supply": BASE_COLS + SUPPLY_COLS,
    "cgc+supply+prices": BASE_COLS + SUPPLY_COLS + PRICE_COLS,
}


# ---------------------------------------------------------------- data

def load_panel(con) -> dict:
    """Weekly panel on a regular 52-week grid per crop year.

    rate[s][y]  kt per 7 days for flow s (NaN where not yet reported)
    cum[s][y]   crop-year-to-date kt at week end
    final[s][y] crop-year total (completed years only)
    level[s][y] week-end stocks by location
    CGC weeks vary in length (week 1 starts Aug 1, week 52 ends Jul 31, the
    holiday report covers two weeks), so flows are converted to a 7-day rate using
    the actual days between week-ending dates.
    """
    ends = con.execute("select distinct crop_year, grain_week, week_ending from gsw").df()
    ends = {(r.crop_year, r.grain_week): r.week_ending for r in ends.itertuples()}
    years = sorted({y for y, _ in ends})

    rows = pd.DataFrame(con.execute(flows_sql("Canola")).fetchall(), columns=["series", "year", "week", "kt"])
    rows = rows[rows["series"].isin(["deliveries", "process"]) | rows["series"].str.startswith("exports_")]
    rows["series"] = rows["series"].replace({"process": "crush"})
    rows["series"] = rows["series"].where(~rows["series"].str.startswith("exports_"), "exports")
    rows = rows.groupby(["series", "year", "week"], as_index=False)["kt"].sum()

    panel: dict = {"years": years, "rate": {}, "cum": {}, "final": {}, "level": {}}
    for s, g in rows.groupby("series"):
        panel["rate"][s], panel["cum"][s], panel["final"][s] = {}, {}, {}
        for y in years:
            reported = sorted(w for (yy, w) in ends if yy == y)
            if not reported:
                continue
            cum_at = g[g["year"] == y].set_index("week")["kt"]
            rate = np.full(WEEKS, np.nan)
            cum = np.full(WEEKS, np.nan)
            prev_w, prev_c = 0, 0.0
            prev_end = pd.Timestamp(int(y[:4]), 7, 31)
            for w in reported:
                c = cum_at.get(w, prev_c)  # a port with no exports yet has no row
                days = (ends[(y, w)] - prev_end).days
                rate[prev_w:w] = (c - prev_c) / days * 7
                cum[prev_w:w] = c
                prev_w, prev_c, prev_end = w, c, ends[(y, w)]
            panel["rate"][s][y], panel["cum"][s][y] = rate, cum
            if max(reported) >= 51 and ends[(y, max(reported))].month == 7:
                panel["final"][s][y] = prev_c

    stocks = con.execute(
        "select location, crop_year, grain_week, kt from commercial_stocks where grain = 'Canola'"
    ).df()
    for loc, g in stocks.groupby("location"):
        panel["level"][loc] = {}
        for y, gy in g.groupby("crop_year"):
            a = np.full(WEEKS, np.nan)
            a[gy["grain_week"].to_numpy() - 1] = gy["kt"].to_numpy()
            # carry levels over unreported weeks (holiday report; 2018-19 has no week 1 stocks)
            panel["level"][loc][y] = pd.Series(a).ffill().bfill().to_numpy()

    # week-end date for every grid week (unreported holiday weeks: previous week + 7 days),
    # and what StatCan and the markets had published by then
    crop = external.load()["crop"]
    panel["date"], panel["supply"], panel["prices"], panel["production_final"] = {}, {}, {}, {}
    for y in years:
        d, prev = [], pd.Timestamp(int(y[:4]), 7, 31)
        for w in range(1, WEEKS + 1):
            prev = pd.Timestamp(ends.get((y, w), prev + pd.Timedelta(days=7)))
            d.append(prev)
        harvest = int(y[:4])
        normal = crop.loc[harvest - 5:harvest - 1, "production_t"].mean()
        panel["date"][y] = d
        panel["supply"][y] = [external.production_known(harvest, day)[0] / normal for day in d]
        panel["prices"][y] = [external.prices_as_of(day) for day in d]
        if harvest in crop.index:
            panel["production_final"][y] = float(crop.loc[harvest, "production_t"])
    return panel


# ------------------------------------------------------------ helpers

def prior_years(panel, y, n=MAX_PRIOR):
    i = panel["years"].index(y)
    return panel["years"][max(0, i - n):i]


def profile(panel, kind, s, y):
    """Mean over up to 5 prior crop years, week by week."""
    arrs = [panel[kind][s][p] for p in prior_years(panel, y) if p in panel[kind][s]]
    if not arrs:
        return np.full(WEEKS, np.nan)
    with np.errstate(all="ignore"):
        return np.nanmean(np.vstack(arrs), axis=0)


def avg(a, t, n):
    """Mean of weeks t-n+1..t (1-indexed week t)."""
    return float(np.nanmean(a[max(0, t - n):t]))


def lg(a, b):
    return float(np.log((a + EPS) / (b + EPS)))


# ------------------------------------------------------------ weekly forecasts

def weekly_rows(panel, s, y, t):
    """Feature rows for target series s, origin week t of year y, one per horizon.
    Returns (rows, targets, baselines); target/baselines are kt per 7 days."""
    r = panel["rate"][s][y]
    if t < 4 or np.isnan(r[t - 1]):
        return []
    prof = profile(panel, "rate", s, y)
    other = "exports" if s == "crush" else "crush"
    a4, a13 = avg(r, t, 4), avg(r, t, 13)
    base4 = avg(prof, t, 4)
    last = panel["rate"][s].get(prior_years(panel, y, 1)[0]) if prior_years(panel, y, 1) else None
    common = {
        "t": t,
        "mom_1": lg(r[t - 1], a4),
        "mom_13": lg(a4, a13),
        "vs_profile": lg(a4, base4),
        "ytd_pace": lg(panel["cum"][s][y][t - 1], profile(panel, "cum", s, y)[t - 1]),
        "other_vs_profile": lg(avg(panel["rate"][other][y], t, 4), avg(profile(panel, "rate", other, y), t, 4)),
        "deliv_vs_profile": lg(avg(panel["rate"]["deliveries"][y], t, 4), avg(profile(panel, "rate", "deliveries", y), t, 4)),
    }
    for loc in panel["level"]:
        lv = panel["level"][loc]
        common[f"stock_{loc}"] = lg(lv[y][t - 1], profile(panel, "level", loc, y)[t - 1])
    common["supply"] = float(np.log(panel["supply"][y][t - 1]))
    common.update({k: panel["prices"][y][t - 1].get(k, np.nan) for k in PRICE_COLS})
    out = []
    for h in HORIZONS:
        k = t + h
        if k > WEEKS:
            break
        seasonal = prof[k - 1] / base4 if base4 > 0 else 1.0
        feats = {**common, "h": h, "season_step": lg(prof[k - 1], base4)}
        base = {
            "last_year": last[k - 1] if last is not None else np.nan,
            "recent_4wk": a4,
            "seasonal_4wk": a4 * seasonal,
        }
        out.append((feats, r[k - 1], base, a4))
    return out


def weekly_backtest(panel, s):
    years = [y for y in panel["years"] if y in panel["final"][s]]
    test_years = years[-TEST_YEARS:]
    train_pool = [y for y in years if prior_years(panel, y)]  # need at least one prior year
    results = []
    for ty in test_years:
        train = [row for y in train_pool if y < ty for t in range(4, WEEKS) for row in weekly_rows(panel, s, y, t)]
        test = [(t, row) for t in range(4, WEEKS) for row in weekly_rows(panel, s, ty, t)]
        models = {name: fit_weekly(train, cols) for name, cols in FEATURE_SETS.items()}
        for t, (feats, actual, base, a4) in test:
            if np.isnan(actual):
                continue
            rec = {"year": ty, "t": t, "h": feats["h"], "actual": actual, **base}
            for name, m in models.items():
                rec[f"ridge[{name}]"] = predict_weekly(m, feats, a4)
            results.append(rec)
    return pd.DataFrame(results)


def fit_weekly(rows, cols):
    X = pd.DataFrame([f for f, *_ in rows])[cols]
    # target: log change from the recent 4-week average
    yv = np.array([lg(actual, a4) for _, actual, _, a4 in rows])
    ok = ~np.isnan(yv) & X.notna().all(axis=1).to_numpy()
    X, yv = X[ok], yv[ok]
    ridge = make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-2, 3, 20))).fit(X, yv)
    return {"cols": cols, "ridge": ridge}


def predict_weekly(models, feats, a4):
    x = pd.DataFrame([feats])[models["cols"]]
    if x.isna().any(axis=None):
        return np.nan
    return max(0.0, (a4 + EPS) * float(np.exp(models["ridge"].predict(x)[0])) - EPS)


# ------------------------------------------------------------ full-year forecasts

def full_year_rows(panel, s, y, t):
    """Baselines and features for the crop-year total of s, from origin week t."""
    cum = panel["cum"][s][y][t - 1]
    priors = [p for p in prior_years(panel, y) if p in panel["final"][s]]
    if np.isnan(cum) or not priors:
        return None
    share = np.mean([panel["cum"][s][p][t - 1] / panel["final"][s][p] for p in priors])
    remaining = np.mean([panel["final"][s][p] - panel["cum"][s][p][t - 1] for p in priors])
    r = panel["rate"][s][y]
    prof = profile(panel, "rate", s, y)
    momentum = avg(r, t, 4) / avg(prof, t, 4) if t >= 4 else np.nan
    finals = [panel["final"][s][p] for p in priors]
    # linear trend through prior crop-year totals, one year ahead (last year's total if only one prior)
    trend = float(np.polyval(np.polyfit(range(len(finals)), finals, 1), len(finals))) if len(finals) >= 3 else finals[-1]
    base = {
        "pace": cum / share,
        "last_year": finals[-1],
        "trend": max(trend, cum),
        "run_rate": cum + remaining * (momentum if t >= 4 else cum / profile(panel, "cum", s, y)[t - 1]),
    }
    base["blend"] = (base["pace"] + base["run_rate"]) / 2
    # share of the crop that ends up as this flow, times the production StatCan has published so far
    # (flow totals are kt, production is t)
    shares = [panel["final"][s][p] * 1000 / panel["production_final"][p] for p in priors if p in panel["production_final"]]
    normal = external.load()["crop"].loc[int(y[:4]) - 5:int(y[:4]) - 1, "production_t"].mean()
    known_kt = panel["supply"][y][t - 1] * normal / 1000
    base["supply_share"] = max(cum, np.mean(shares) * known_kt)
    if s == "exports":
        # exports come out of what crushers leave: production minus the crush trend
        crush_finals = [panel["final"]["crush"][p] for p in priors]
        crush_trend = float(np.polyval(np.polyfit(range(len(crush_finals)), crush_finals, 1), len(crush_finals))) \
            if len(crush_finals) >= 3 else crush_finals[-1]
        left = [panel["final"][s][p] / (panel["production_final"][p] / 1000 - panel["final"]["crush"][p])
                for p in priors if p in panel["production_final"]]
        base["supply_residual"] = max(cum, np.mean(left) * (known_kt - crush_trend))
    else:
        base["supply_residual"] = base["supply_share"]
    feats = {
        "t": t,
        "pace_vs_last": lg(base["pace"], base["last_year"]),
        "run_vs_pace": lg(base["run_rate"], base["pace"]),
        "ytd_pace": lg(cum, profile(panel, "cum", s, y)[t - 1]),
        "deliv_pace": lg(panel["cum"]["deliveries"][y][t - 1], profile(panel, "cum", "deliveries", y)[t - 1]),
        "stocks_all": lg(sum(panel["level"][l][y][t - 1] for l in panel["level"]),
                         sum(profile(panel, "level", l, y)[t - 1] for l in panel["level"])),
    }
    return feats, base


COMBO_INPUTS = ["last_year", "trend", "pace", "run_rate", "supply_share", "supply_residual"]
COMBO_SETS = {"combo": COMBO_INPUTS, "combo[no supply]": COMBO_INPUTS[:4]}


def fit_combo(rows, inputs):
    """Per origin week, the non-negative weights over `inputs` (summing to 1, steps of 0.1)
    with the lowest mean absolute % error on training years, pooling origins within 3 weeks."""
    df = pd.DataFrame(rows)
    grid = np.array([w for w in np.ndindex(*[11] * len(inputs)) if sum(w) == 10]).T / 10
    weights = {}
    for t in range(1, WEEKS):
        g = df[(df["t"] - t).abs() <= 3]
        if g.empty:
            continue
        err = np.abs(g[inputs].to_numpy() @ grid - g[["actual"]].to_numpy()) / g[["actual"]].to_numpy()
        weights[t] = grid[:, int(np.argmin(err.mean(axis=0)))]
    return weights


def full_year_training_rows(panel, s, years):
    rows = []
    for y in years:
        for t in range(1, WEEKS):
            row = full_year_rows(panel, s, y, t)
            if row:
                rows.append({"year": y, "t": t, "actual": panel["final"][s][y], **row[1]})
    return rows


def full_year_backtest(panel, s):
    years = [y for y in panel["years"] if y in panel["final"][s]]
    test_years = years[-TEST_YEARS:]
    results = []
    for ty in test_years:
        train = full_year_training_rows(panel, s, [y for y in years if y < ty])
        weights = {name: fit_combo(train, inputs) for name, inputs in COMBO_SETS.items()}
        for rec in full_year_training_rows(panel, s, [ty]):
            for name, inputs in COMBO_SETS.items():
                w = weights[name].get(rec["t"])
                rec[name] = float(np.array([rec[k] for k in inputs]) @ w) if w is not None else np.nan
            results.append(rec)
    return pd.DataFrame(results)


# ------------------------------------------------------------ scoring + current forecast

def score(df, methods, by):
    out = {}
    for key, g in df.groupby(by):
        out[key] = {m: float(np.nanmean(np.abs(g[m] - g["actual"]) / g["actual"].clip(lower=1)) * 100) for m in methods}
    return out


def weekly_mae(df, methods):
    return {h: {m: float(np.nanmean(np.abs(g[m] - g["actual"]))) for m in methods} for h, g in df.groupby("h")}


def main() -> None:
    con = connect()
    panel = load_panel(con)
    cur = panel["years"][-1]
    t_now = int(np.max(np.where(~np.isnan(panel["rate"]["crush"][cur]))[0]) + 1)
    report = {"generated": dt.date.today().isoformat(), "current_year": cur, "origin_week": t_now,
              "test_years": [y for y in panel["years"] if y in panel["final"]["crush"]][-TEST_YEARS:],
              "weekly": {}, "full_year": {}}
    prod, source = external.production_known(int(cur[:4]), panel["date"][cur][t_now - 1])
    vint = external.load()["vintages"]
    rel = vint[(vint["harvest_year"] == int(cur[:4])) & (vint["release"] == source)]
    report["supply"] = {"production_t": prod, "source": source,
                        "released": rel["released"].max().date().isoformat() if len(rel) else None,
                        "last_year_t": float(external.load()["crop"].loc[int(cur[:4]) - 1, "production_t"])}

    wk_methods = ["last_year", "recent_4wk", "seasonal_4wk", *[f"ridge[{n}]" for n in FEATURE_SETS]]
    fy_methods = ["last_year", "trend", "pace", "run_rate", "supply_share", "supply_residual", *COMBO_SETS]
    for s in TARGETS:
        wb = weekly_backtest(panel, s)
        mae = weekly_mae(wb, wk_methods)
        fb = full_year_backtest(panel, s)
        fb["bucket"] = pd.cut(fb["t"], [0, 4, 8, 13, 26, 39, 52], labels=["1-4", "5-8", "9-13", "14-26", "27-39", "40-51"])
        mape = score(fb, fy_methods, "bucket")

        print(f"\n=== {s.upper()} weekly: mean absolute error, kt/week (lower is better), test years {report['test_years'][0]}..{report['test_years'][-1]}")
        print(pd.DataFrame(mae).T.round(1).to_string())
        print(f"\n=== {s.upper()} full-year total: mean absolute % error by origin week")
        print(pd.DataFrame(mape).T.round(1).to_string())

        # current forecast, trained on every completed year
        years = [y for y in panel["years"] if y in panel["final"][s]]
        train = [row for y in years if prior_years(panel, y) for t in range(4, WEEKS) for row in weekly_rows(panel, s, y, t)]
        best_wk = min(wk_methods[1:], key=lambda m: np.mean([mae[h][m] for h in HORIZONS]))
        if best_wk.startswith("ridge["):
            models = fit_weekly(train, FEATURE_SETS[best_wk[6:-1]])
        # empirical 80% interval from backtest errors of the chosen method, per horizon
        wb["ratio"] = (wb["actual"] + EPS) / (wb[best_wk] + EPS)
        weekly_now = []
        for feats, _, base, a4 in weekly_rows(panel, s, cur, t_now):
            point = predict_weekly(models, feats, a4) if best_wk.startswith("ridge[") else base[best_wk]
            q = wb.loc[wb["h"] == feats["h"], "ratio"].quantile([0.1, 0.9]).to_numpy()
            weekly_now.append({"week": t_now + feats["h"], "forecast": round(float(point), 1),
                               "low": round(float((point + EPS) * q[0] - EPS), 1), "high": round(float((point + EPS) * q[1] - EPS), 1),
                               **{k: round(v, 1) for k, v in base.items()}})

        # full-year: the combination, with an 80% range from its backtest errors near this origin week
        best_fy = "combo"
        fb["fy_ratio"] = fb["actual"] / fb[best_fy]
        _, base = full_year_rows(panel, s, cur, t_now)
        near = fb[(fb["t"] - t_now).abs() <= 2]["fy_ratio"].quantile([0.1, 0.9]).to_numpy()
        weights = fit_combo(full_year_training_rows(panel, s, years), COMBO_INPUTS)[t_now]
        point = float(np.array([base[k] for k in COMBO_INPUTS]) @ weights)
        report["weekly"][s] = {"mae_by_horizon": mae, "best": best_wk, "forecast": weekly_now}
        # per-year full-year error at a few origin weeks, for the write-up
        g = fb[fb["t"].isin([4, 8, 13, 26])]
        report["full_year_by_year"] = report.get("full_year_by_year", {})
        report["full_year_by_year"][s] = {
            m: g.assign(e=100 * (g[m] / g["actual"] - 1)).pivot(index="year", columns="t", values="e").round(1).to_dict()
            for m in ["combo", "combo[no supply]"]}
        print("\n    full-year % error by test year (combo vs no supply), origin weeks 4/8/13/26:")
        print(pd.concat({m: g.assign(e=100 * (g[m] / g["actual"] - 1)).pivot(index="year", columns="t", values="e")
                         for m in ["combo[no supply]", "combo"]}, axis=1).round(1).to_string())
        report["full_year"][s] = {"mape_by_origin": {str(k): v for k, v in mape.items()}, "best": best_fy,
                                  "forecast": round(point), "low": round(point * near[0]), "high": round(point * near[1]),
                                  "weights": dict(zip(COMBO_INPUTS, map(float, weights))),
                                  "baselines": {k: round(v) for k, v in base.items()},
                                  "last_year_actual": round(panel["final"][s][years[-1]])}
        print(f"\n--> {s}: best weekly method = {best_wk}; best full-year method = {best_fy}")
        print(f"    next weeks: {[(f['week'], f['forecast'], f['low'], f['high']) for f in weekly_now]}")
        fy = report["full_year"][s]
        print(f"    supply known now: {external.production_known(int(cur[:4]), panel['date'][cur][t_now - 1])}")
        print(f"    full year {cur}: {fy['forecast']:,} kt (80% range {fy['low']:,}-{fy['high']:,}); weights {fy['weights']}; baselines {fy['baselines']}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=1, default=float)
    OUT_PATH.write_text(text)
    DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_PATH.write_text(text)
    print(f"\nWrote {OUT_PATH.relative_to(ROOT)} and {DASHBOARD_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
