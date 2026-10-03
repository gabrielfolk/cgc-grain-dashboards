# Feed use estimate: method and backtest

Built by `.venv/bin/python src/feed_data.py` into `dashboard/feed/data.json` and shown on the Feed Grains page.
Figures below are from the 2026-27 build (CGC week 7; StatCan tables as of September 2026).

## What is being estimated

Crop-year feed use in Canada, with a West / East split:
- **Barley, wheat (excluding durum), durum and oats:** StatCan's "animal feed, waste and dockage" for Canada
  (table 32-10-0013).
- **Corn:** StatCan's Canada corn feed (table 32-10-0014, Sep–Aug corn year).

StatCan derives feed use as a residual of its supply and disposition balance, so it also absorbs waste, dockage
and measurement error. It is published cumulatively for Aug–Dec, Aug–Mar and the full crop year, and **for Canada
only**.

## Regional split (estimated)

West = MB, SK, AB, BC. East = Ontario, Quebec and the Atlantic provinces. West + East = Canada.

- **Small grains:** each region's on-farm feed (32-10-0015, published by region) plus a share of the rest of
  Canada's feed use (fed off the farm that grew it) in proportion to the region's production (32-10-0359).
  - Scaling the Canada total by on-farm shares alone was rejected. Only ~13% of wheat feed is on-farm, and Atlantic
    farms feed more wheat on-farm than Ontario and Quebec, so that would have given the Atlantic provinces ~0.6 Mt
    of wheat feed, more than they grow.
  - Assumes off-farm feed grain is mostly used in the region that grew it.
- **Corn:** provincial corn feed has been suppressed since 2007-08.
  - West = domestic use in "other provinces" (= Canada less Ontario and Quebec, so the West plus the Atlantic
    provinces), less seed and the Atlantic crop (assumed fed where grown). It includes any western industrial use
    (Minnedosa ethanol).
  - East = Canada corn feed less West.

2025-26: West 14.0 Mt, East 6.7 Mt (of which corn 5.9 Mt).

## Method

1. **Total:** the average total feed use (barley, wheat, durum, oats and corn fed in the West) of the last five
   completed crop years.
2. **Model shares:** the total is split across those grains with a model fitted on past crop years:

   `log(share_i / share_barley) = crop constant + a × log(availability ratio) + b × log(energy-adjusted price ratio)`

   - **Availability:** supply (carry-in + production + imports) relative to that grain's own average.
     Western corn is treated as freely available through imports.
   - **Energy-adjusted price:** the crop-year average Alberta farm price divided by feeding value relative to barley:
     wheat 1.08, durum 1.06, oats 0.85, corn 1.12. Western corn is US corn delivered to southern Alberta
     (CBOT + US$1.60/bu basis and freight, at the monthly USD/CAD).
   - Fitted on 2013-14 to 2025-26: **a = 2.40**, **b = −0.52**.
3. **Estimate:** 50% model and 50% each grain's own five-year average.
4. **Eastern corn:** its five-year average. Putting all Canada corn in the share model
   flipped the price effect to the wrong sign (b = +0.17 with Ontario corn prices, +0.13 with US corn). Eastern
   corn is fed from the local crop to hogs, poultry and dairy and doesn't trade off against western barley on price.
   It was taken out on that economic ground, not on backtest score.
5. **Regions:** each small grain's estimate × the region's five-year average share of it; western corn from the model,
   eastern corn as above.

**For the current crop year:**
- Supply is carry-in from StatCan's July ending stocks, plus StatCan's latest production estimate (Canada), plus
  last year's imports.
- Prices are the latest month StatCan has published (about two months behind), and this week's CBOT close for corn.
- Where StatCan hasn't yet published August corn for the latest complete year, the March figure is scaled by the
  usual March-to-August ratio.

## Backtest

Each crop year from 2019-20 to 2025-26 was estimated using only earlier years. Mean absolute error, kt:

| | Estimate (50/50) | Model only | 5-yr average | Last year |
|---|---|---|---|---|
| Barley | 467 | **463** | 713 | 960 |
| Wheat (ex-durum) | 937 | 1,270 | **604** | 777 |
| Durum | 157 | **108** | 276 | 146 |
| Oats | **253** | 302 | 270 | 330 |
| Corn, Canada | 1,031 | 1,257 | **804** | 1,385 |
|   fed in the West | 1,014 | 1,367 | **908** | 1,772 |
|   fed in the East | **527** | 527 | 527 | 621 |
| Total | **1,436** | 1,436 | 1,436 | 1,586 |
| Western Canada total | 1,355 | 1,356 | **1,351** | 1,749 |
| East total | 654 | 692 | 610 | **482** |

- **The blend beats both baselines** for barley, durum and oats.
- **Wheat and western corn are the weak spots;** the five-year average does better there.
  - Wheat feed use swings with crop quality and export demand, which the model doesn't capture.
  - Western corn now includes the Manitoba crop, not just imports. In the previous (imports-only) version the
    blend beat the five-year average for corn.
- **Regional totals:** in the West the estimate ties the five-year average. In the East last year's level is the
  best guess: eastern feed use moves slowly.
- **Scaling the total by livestock numbers made the total less accurate**, so the page shows the livestock demand
  index (now for both regions) as context only.

## 2026-27 estimate (week 7 build)

Total **20.1 Mt**: West 13.7 Mt, East 6.5 Mt.

| Grain | Estimate |
|---|---|
| Corn | 9.21 Mt (West 3.72, East 5.49) |
| Barley | 5.57 Mt |
| Wheat (ex-durum) | 4.12 Mt |
| Oats | 0.82 Mt |
| Durum | 0.41 Mt |

## Limits and next steps

- **The regional split is an estimate,** resting on the on-farm data and the production-share assumption for
  off-farm feed. Ontario feed mills buying western grain, or western feedlots buying eastern grain, would move it.
- **Crop quality** enters only through availability and price. CGC's harvest-sample grade distributions would add
  a direct measure; see TODO.md.
- **Livestock data** is annual or twice a year. AAFC's weekly slaughter data would be timelier; see TODO.md.
- **The energy values and the corn basis are assumptions.** The page lets analysts change them for the price
  comparison, but the estimate uses the defaults.
- **Re-score each season.** Seven test years is a small sample.
