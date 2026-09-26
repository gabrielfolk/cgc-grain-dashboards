# Feed use estimate: method and backtest

Built by `.venv/bin/python src/feed_data.py` into `dashboard/feed/data.json` and shown on the Western Feed Grains page.
Figures below are from the 2026-27 build (CGC week 7; StatCan tables as of September 2026).

## What is being estimated

The target is crop-year feed use of five grains:
- **Barley, wheat (excluding durum), durum and oats:** StatCan's "animal feed, waste and dockage" for Canada
  (table 32-10-0013). Most feeding happens in Western Canada; StatCan's farm table 32-10-0015 puts about three-quarters
  of barley feeding on western farms.
- **Corn:** StatCan's imports into provinces other than Ontario and Quebec (table 32-10-0014), used as a proxy for corn
  fed in Western Canada. Ontario and Quebec feed their own corn crop.

StatCan derives feed use as a residual of its supply and disposition balance, so it also absorbs waste, dockage
and measurement error. It is published cumulatively for Aug–Dec, Aug–Mar and the full crop year.

## Method

1. **Total:** the average total feed use (all five grains) of the last five completed crop years.
2. **Model shares:** the total is split across grains with a model fitted on past crop years:

   `log(share_i / share_barley) = crop constant + a × log(availability ratio) + b × log(energy-adjusted price ratio)`

   - **Availability:** supply (carry-in + production + imports) relative to that grain's own average.
     Corn is treated as freely available through imports.
   - **Energy-adjusted price:** the crop-year average Alberta farm price divided by feeding value relative to barley:
     wheat 1.08, durum 1.06, oats 0.85, corn 1.12.
     - Barley uses StatCan's feed barley price and wheat its non-milling ("other") wheat price.
     - Delivered US corn = CBOT + US$1.60/bu basis and freight, converted at the monthly USD/CAD.
   - Fitted on 2013-14 to 2025-26: **a = 2.81**, **b = −0.95**. More supply and a lower relative price both raise
     a grain's share.
3. **Estimate:** 50% model and 50% each grain's own five-year average. With so few years, averaging with the
   baseline guards against the model overreacting.

**For the current crop year:**
- Supply is carry-in from StatCan's July ending stocks, plus StatCan's latest production estimate, plus last
  year's imports.
- Prices are the latest month StatCan has published (about two months behind), and this week's CBOT close for corn.
- Where StatCan hasn't yet published August corn imports for the latest complete year, the March figure is scaled
  by the usual March-to-August ratio.

## Backtest

Each crop year from 2019-20 to 2025-26 was estimated using only earlier years. Mean absolute error, kt:

| Grain | Estimate (50/50) | Model only | 5-yr average | Last year |
|---|---|---|---|---|
| Barley | **483** | 474 | 713 | 960 |
| Wheat (ex-durum) | 929 | 1,254 | **604** | 777 |
| Durum | 152 | **135** | 276 | 146 |
| Oats | 289 | 388 | **270** | 330 |
| Imported corn (West) | **917** | 1,003 | 1,128 | 1,747 |
| Total | 1,474 | 1,474 | 1,474 | 1,825 |

- **The blend beats both baselines overall.** Barley, corn and durum respond to supply and price.
- **Wheat is the weak spot.** Its feed use swings with crop quality and export demand, which the model doesn't
  capture; the five-year average does better there.
- **Scaling the total by livestock numbers made the total less accurate** (tested on the same years), so the page
  shows the livestock demand index as context only.
- **Variants tried and why this one was chosen:**
  - price-only, availability-only and shrunk (ridge) versions of the model
  - totals scaled by the demand index
  - the 50/50 blend was adopted as a standard small-sample safeguard, not tuned to these test years

## 2026-27 estimate (week 7 build)

Total **12.6 Mt**:

| Grain | Estimate |
|---|---|
| Barley | 5.60 Mt |
| Wheat (ex-durum) | 3.82 Mt |
| Imported corn (West) | 1.96 Mt |
| Oats | 0.78 Mt |
| Durum | 0.48 Mt |

The corn estimate is high partly because the five-year average (2021-22 to 2025-26) includes the drought-year
imports of 2021-22.

## Limits and next steps

- **Crop quality** enters only through availability and price. CGC's harvest-sample grade distributions would add
  a direct measure; see TODO.md.
- **Livestock data** is annual or twice a year. AAFC's weekly slaughter data would be timelier; see TODO.md.
- **The energy values and the corn basis are assumptions.** The page lets analysts change them for the price
  comparison, but the estimate uses the defaults.
- **Re-score each season.** Seven test years is a small sample.
