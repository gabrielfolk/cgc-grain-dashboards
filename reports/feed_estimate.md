# Feed use: demand model and StatCan residual

Built by `.venv/bin/python src/feed_data.py` (with `src/feed_model.py`) into `dashboard/feed/data.json` and shown
on the Feed Grains page. Figures below are from the 2026-27 build (CGC week 8; StatCan tables as of October 2026).

The page has two measures of feed use:

1. **The feed demand model** (the headline): grain fed, built from animal numbers and feeding rates.
2. **StatCan's "animal feed, waste and dockage"**, the residual of its supply and disposition balance, with a
   forecast of what StatCan will print for the current year.

# Part 1: Feed demand model

`src/feed_model.py`. Grain fed = animals (by class and province) × grain fed per animal, for barley, wheat
(including durum), oats and corn. It does not use StatCan's feed residual.

## Feeding rates

**Base:** StatCan, [Livestock Feed Requirements Study 1999–2001](https://www150.statcan.gc.ca/n1/pub/23-501-x/23-501-x2003001-eng.pdf)
(catalogue 23-501-X, table 7), the latest edition (January 2003). Grain fed per animal by class, province and
ingredient, supplied by provincial livestock specialists, the Animal Nutrition Association of Canada, marketing
boards and feed mills, "meant to reflect what was actually fed". Saved in `reference/statcan_livestock_feed_1999.csv`.

**Updated where feeding has changed:**

| Class | Rate | Source |
|---|---|---|
| Cattle on feeding operations | 18.5 lb barley/day, every day of the year | [Manitoba Agriculture, Cost of Production: Beef Feedlot Finishing (2026)](https://www.gov.mb.ca/agriculture/farm-management/cost-production/pubs/cop-beef-feedlot-finishing.pdf): 650 to 1,400 lb over 231 days |
| Backgrounders (feeder and stocker operations, January 1) | 6.5 lb barley/day for 160 days | [Manitoba Agriculture, Cost of Production: Beef Backgrounding (2026)](https://www.gov.mb.ca/agriculture/farm-management/cost-production/pubs/cop-beef-backgrounding.pdf): 5 to 8 lb/day, 500 to 900 lb |
| Hogs | 1999 rate × 1.09 finishing, × 0.56 nursery, × 1.25 sows | [Manitoba Agriculture, Cost of Production: Swine Farrow-Finish (2025)](https://www.gov.mb.ca/agriculture/farm-management/cost-production/pubs/cop-swine-farrow-finish.pdf): 279.3 kg feed per pig grown 26 to 123 kg, 33.2 kg nursery, sows 3.0 kg/day dry and 6.5 kg/day lactating; against Manitoba's 1999 rates |
| Dairy cows | 1999 rate × milk sold per cow against 1999 (+55% by 2025) | StatCan 32-10-0113 and 32-10-0130. [Alberta Agriculture, Dairy Feed Costs (2021)](https://open.alberta.ca/dataset/bd8da099-da84-4544-8613-bb64e79712ab/resource/019a53b4-3f5c-4a3d-b17c-55d0d823ef45/download/af-explained-in-brief-dairy-feed-costs-dairy-2-2021-01.pdf): milk per kg of concentrate flat at 2.2 litres, 1997–2019 |
| Broilers, turkeys | 1999 rate per kg of meat produced | StatCan 32-10-0117 (1999 kg per bird by province) |
| Beef cows, bulls, replacement heifers, veal calves, layers, sheep | 1999 rate | |

Horses, other poultry and fish are left out (no current counts; under 0.4 Mt in 1999).

The per-animal amounts have held up. For example, the 2026 Manitoba feedlot guide works out to 1.94 t of barley per
finished steer, against Alberta's 1999 rate of 2.01 t of complete ration (1.77 t of the four feed grains, the rest
protein meal, peas and screenings). The 2025 hog guide comes to about 360 kg of feed per
market pig, against about 358 kg in 1999: heavier pigs have been offset by better feed conversion. The *mix* has
changed more (see below).

## Animal numbers

| Class | StatCan table | Basis |
|---|---|---|
| Cattle by class, cattle on feeding and on feeder/stocker operations | 32-10-0130 | crop-year average of July 1 and January 1 (backgrounders: January 1) |
| Sows, boars | 32-10-0160 | July 1 and January 1 |
| Pig crop; pigs finished in province = pig crop + interprovincial imports − interprovincial and international exports − deaths | 32-10-0200 | calendar year |
| Chicken and turkey meat | 32-10-0117 | calendar year |
| Layers | 32-10-0121 | calendar-year average |
| Veal calves | 32-10-0125 (Canada), split by the 1999 provincial shares | calendar year |
| Sheep | 32-10-0129 | July 1 and January 1 |

For the current crop year, January inventories aren't out yet (July only), and the pig crop, poultry meat and milk use
the latest full calendar year.

## Grain mix

- Each class keeps its 1999 provincial mix of barley, wheat, oats and corn, except western feedlot and backgrounding
  cattle: 85% barley, 8% wheat, 7% corn (the Manitoba guides feed barley; western finishing diets are typically over
  80% barley).
- The mix shifts each crop year with relative energy-adjusted prices: share ∝ base share × (price ÷ energy value ÷
  its long-run average)^−1. Alberta prices and US corn delivered to southern Alberta in the West, Ontario prices in
  the East.
- **Western corn** is set to StatCan's measured corn use in the provinces outside Ontario and Quebec (production,
  imports and stock changes, 32-10-0014) where published, because the 1999 rations predate Manitoba's corn crop
  (about 0.5 Mt then, over 2 Mt now). For years not yet published, it is projected from the last three years' share,
  shifted by price. The rest of the West's demand is split among barley, wheat and oats.

## What it shows

| Crop year | Model, Canada | West | East | StatCan residual | West (est. split) | East (est. split) |
|---|---|---|---|---|---|---|
| 2021-22 | 18.7 Mt | 9.0 | 9.7 | 22.1 | 15.3 | 6.7 |
| 2022-23 | 18.9 | 9.2 | 9.7 | 19.8 | 12.8 | 7.0 |
| 2023-24 | 18.8 | 9.1 | 9.7 | 20.1 | 14.3 | 5.7 |
| 2024-25 | 18.7 | 9.0 | 9.7 | 18.0 | 12.1 | 5.9 |
| 2025-26 | 18.9 | 9.0 | 9.9 | 20.7* | 14.0 | 6.7 |
| **2026-27** | **19.2** | **9.3** | **9.9** | not yet published | | |

\* corn estimated until StatCan publishes August.

- **Same level, much less noise.** From 2012-13 to 2025-26 the model averages 18.6 Mt and StatCan's residual 19.6 Mt.
  But the residual's standard deviation is 1.54 Mt against the model's 0.38 Mt: it swings about four times as much
  as livestock numbers can explain. Those swings are waste, dockage and balancing error, not feeding.
- **The regions disagree in opposite directions.** Western livestock need about 9.2 Mt, while the West's residual
  averages 3.5 Mt more. Eastern livestock need about 9.6 Mt, while the East's residual averages 2.4 Mt less. That
  points to western grain moving east, dockage and waste counted in the western residual, and eastern co-products
  (below).
- **2026-27 by grain:** corn 11.9 Mt (West 3.9, East 8.1), barley 5.1, wheat 1.8, oats 0.3. By livestock (barley
  equivalent): hogs 35%, feedlot and backgrounding cattle 24%, dairy 20%, poultry 17%, cow herd 5%.

## Limits

- **Co-products aren't netted out.** Distillers' grains from ethanol plants, wheat millfeeds and bakery waste replace
  some grain, mostly in Ontario and Quebec. So the model's eastern corn need (8.1 Mt) runs above the East's corn feed in
  StatCan's balance (about 5.9 Mt).
- **The mix is the weakest part.** It does not track crop quality, so a year with a lot of feed-grade wheat shows up
  only through its price; western wheat is likely understated (model about 0.8 Mt against a residual near 3 Mt,
  some of which is dockage).
- **The rates are from 1999,** updated for the classes above. A new StatCan study or provincial ration surveys would
  replace them.
- **No backtest:** there is no independent measure of feed actually fed to score the model against. StatCan's
  residual is shown next to it, not as a target.

# Part 2: StatCan's residual and a forecast of it

StatCan derives feed use as a residual of its supply and disposition balance, so it also absorbs waste, dockage
and measurement error. It is published cumulatively for Aug–Dec, Aug–Mar and the full crop year, and **for Canada
only**. The page forecasts it for the current crop year ("what StatCan will likely print") and splits it by region.

### What is being forecast

Crop-year feed use in Canada, with a West / East split:
- **Barley, wheat (excluding durum), durum and oats:** StatCan's "animal feed, waste and dockage" for Canada
  (table 32-10-0013).
- **Corn:** StatCan's Canada corn feed (table 32-10-0014, Sep–Aug corn year).

StatCan derives feed use as a residual of its supply and disposition balance, so it also absorbs waste, dockage
and measurement error. It is published cumulatively for Aug–Dec, Aug–Mar and the full crop year, and **for Canada
only**.

### Regional split (estimated)

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

### Method

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

### Backtest

Each crop year from 2019-20 to 2025-26 was estimated using only earlier years. Mean absolute error against
StatCan's published residual, kt:

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
- **Scaling the total by a rough livestock index made the total less accurate.** The demand model in part 1 is the
  proper livestock-based measure.

### 2026-27 forecast (week 8 build)

Total **20.1 Mt**: West 13.7 Mt, East 6.5 Mt. Corn 9.24 Mt, barley 5.56 Mt, wheat (ex-durum) 4.11 Mt, oats 0.82 Mt,
durum 0.41 Mt.

### Limits

- **The regional split is an estimate,** resting on the on-farm data and the production-share assumption for
  off-farm feed. Ontario feed mills buying western grain, or western feedlots buying eastern grain, would move it.
- **Crop quality** enters only through availability and price. CGC's harvest-sample grade distributions would add
  a direct measure; see TODO.md.
- **The energy values and the corn basis are assumptions.** The page lets analysts change them for the price
  comparison, but the estimate uses the defaults.
- **Re-score each season.** Seven test years is a small sample.
