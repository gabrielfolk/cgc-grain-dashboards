# Canola crush and export forecasts

Run: `.venv/bin/python src/canola_forecast.py` (about 20 seconds after the first run caches external data).
It writes `reports/canola_forecast.json` and `dashboard/canola/forecast.json`, which the Canola Pipeline page reads.
Results below are from 2026-27 week 7 data (week ending 2026-09-20).

## Data

- **CGC Grain Statistics Weekly:** crush, exports (port terminals plus direct shipments), producer deliveries, and
  commercial stocks by location, for crop years 2013-14 to 2026-27.
- **StatCan canola production** (`src/external.py`). The backtest only sees what StatCan had published by each week:
  - Before the first in-season estimate: seeded area × the average yield of the 5 previous harvests.
  - Late August to early December: StatCan's in-season estimates (July survey before 2020, satellite-model
    estimates since), as released. These are recorded with source links in `reference/statcan_canola_vintages.csv`.
  - From early December: the final table value. The November survey is usually within a few percent of the
    final revision, so this is a small look-ahead.
- **Prices (tested, not used).** There is no free ICE canola futures history. The proxies tested were:
  - CBOT soybean oil, meal and soybeans, plus USD/CAD (Yahoo Finance)
  - StatCan's monthly Saskatchewan canola farm price, lagged to its approximate release date

  From these came a crush-margin proxy, the soybean oil share, canola's price against soybeans, and price changes.

## Method

- **Weekly rates.** CGC weeks vary in length (week 1 starts Aug 1, week 52 ends Jul 31, and one holiday report
  covers two weeks), so every flow is converted to kt per 7 days.
- **Backtest.** Each test crop year from 2018-19 to 2025-26 is forecast from every week, using models trained only
  on earlier crop years.
- **Weekly model.** A ridge regression forecasts 1–4 weeks ahead. It predicts the log change from the recent
  4-week average, using momentum, the usual seasonal change, crop-year pace, the other flow's pace, delivery pace,
  and stocks by location. Three feature sets were compared: CGC data only, plus supply, and plus prices.
- **Full-year model.** A per-week weighted combination of simple estimates:
  - last year's total
  - trend
  - pace
  - run rate
  - **StatCan supply share:** the latest production estimate × the share of the crop that usually becomes this flow
  - **supply after crush** (exports only): production minus the crush trend, × the usual export share of that remainder

  The weights are non-negative, sum to 1, and are learned from earlier crop years within ±3 weeks of each origin week.

## Results

Weekly, mean absolute error in kt/week:

| | Last 4 weeks | CGC data | + supply | + supply + prices |
|---|---|---|---|---|
| Crush, 1 week ahead | 25.9 | **25.5** | 25.7 | 27.2 |
| Crush, 4 weeks ahead | 26.9 | **26.0** | 26.3 | 27.5 |
| Exports, 1 week ahead | 61.4 | 56.8 | **56.4** | 59.3 |
| Exports, 4 weeks ahead | 66.1 | 61.5 | **59.8** | 63.5 |

- **Supply** helps weekly exports, mostly at longer horizons.
- **The price proxies made both flows worse**, so they are not used. Real ICE canola futures and canola oil and
  meal prices may behave differently. The proxies are soybean-based, and canola's own price is monthly and lagged.

Full-year total, mean absolute % error by origin week:

| Origin week | Crush: combination | Crush: without supply | Exports: combination | Exports: without supply | Exports: StatCan supply only |
|---|---|---|---|---|---|
| 1–4 | **8.3** | **8.3** | 26.7 | 26.4 | **24.1** |
| 5–8 | 5.9 | **5.7** | 20.4 | 23.2 | **12.6** |
| 9–13 | **5.1** | 5.4 | 20.2 | 24.1 | **11.3** |
| 14–26 | **3.9** | 4.6 | 15.6 | 21.8 | **13.7** |
| 27–39 | **2.4** | 2.9 | **6.2** | 13.9 | 15.1 |
| 40–51 | **1.3** | 1.6 | **2.0** | 3.0 | 14.4 |

- **Supply data is the big gain for exports.** From week 5 to week 26, the StatCan supply estimate on its own halves
  the error of methods that ignore it. It cut the 2021-22 drought miss from +94% to +26% by week 8.
- **The combination does not yet use supply fully early in the year.** It learns its weights from earlier years,
  and before 2021-22 those contain no drought. From week 27 on, the combination is best.
- **I have not switched to "supply only" for weeks 5–26.** Choosing a method from these same test results would
  overstate how accurate it is. As more years accumulate, the learned weights should move toward supply on their own.
- **Crush** gains a little from supply (3.9% against 4.6% in weeks 14–26). Crush is limited by plant capacity more
  than by crop size.

## Current forecast (2026-27, from week 7; StatCan August estimate 22.05 Mt, released Sep 16)

| | Weeks 8–11, kt/week | Full crop year |
|---|---|---|
| Crush | 203, 194, 204, 209 | **11.4 Mt** (80% range 9.9–12.7); last year 11.0 Mt |
| Exports | 84, 144, 157, 160 | **9.1 Mt** (80% range 4.7–10.8); last year 9.1 Mt |

The StatCan-supply-only estimate for exports is also 9.1 Mt.

## Next steps

- **Get real ICE canola futures and canola oil and meal prices**, for example from Barchart or an exchange data
  feed, and re-test the price features.
- **Add StatCan's September model estimate and the on-farm stock estimates** (table 32-10-0007) to the supply input.
- **Re-score each season.** Eight test years is a small sample.
