# Feed use: our estimate by commodity, and StatCan's residual

Built by `.venv/bin/python src/feed_data.py` (with `src/feed_model.py`) into `dashboard/feed/data.json` and shown
on the Feed Grains page. Figures below are from the 2026-27 build (CGC week 8; StatCan tables as of October 2026).

The page has one estimate, the model in Part 1: domestic feed use by commodity (barley, wheat, durum, oats,
Canadian and US corn) for Western and Eastern Canada, and how it is built. StatCan's "animal feed, waste and
dockage", the residual of its supply and disposition balance, is shown next to it as the reference (Part 2).

# Part 1: Feed demand model

`src/feed_model.py`, with inputs assembled in `src/feed_data.py`. Three steps, by province and livestock class:

1. **Grain energy needed** = animals × grain fed per head (barley-equivalent), less co-products. Animal numbers
   include slaughter and live exports for cattle on feed; grain per fed animal moves with carcass weight.
2. **Split across grains** (barley, wheat ex-durum, durum, oats, corn) from each class's ration, shifted by relative
   energy-adjusted price and by availability (supply).
3. **Corn by origin:** Canadian-grown or US imports.

**The model does not use StatCan's feed residual anywhere** (since 2026-10-06). StatCan's "animal feed, waste and
dockage" is the balancing item of its supply and disposition tables, not a feed estimate, so it is shown only for
comparison. Inputs are measured data (animal inventories, slaughter, trade, production, stocks, prices), published
feeding rates and rations, and StatCan's farm survey of grain fed on farms (reported by farmers).

## Feeding rates

**Base:** StatCan, [Livestock Feed Requirements Study 1999–2001](https://www150.statcan.gc.ca/n1/pub/23-501-x/23-501-x2003001-eng.pdf)
(catalogue 23-501-X, table 7), the latest edition (January 2003). Grain fed per animal by class, province and
ingredient, supplied by provincial livestock specialists, the Animal Nutrition Association of Canada, marketing
boards and feed mills, "meant to reflect what was actually fed". Saved in `reference/statcan_livestock_feed_1999.csv`.

**Updated where feeding has changed:**

| Class | Rate | Source |
|---|---|---|
| Cattle on feed | fed cattle marketed × days on feed × 18.5 lb barley/day (see Animal numbers) | [Manitoba Agriculture, Cost of Production: Beef Feedlot Finishing (2026)](https://www.gov.mb.ca/agriculture/farm-management/cost-production/pubs/cop-beef-feedlot-finishing.pdf): 18.5 lb/day rolled barley |
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
| Pig crop; pigs finished in province = pig crop + interprovincial imports − interprovincial and international exports − deaths | 32-10-0200 | July to June (half-years); not yet published: the latest July–June × AAFC weekly hog slaughter growth |
| Chicken and turkey meat | 32-10-0117 | calendar year put on the crop year with AAFC weekly poultry slaughter by region (eviscerated weight); Atlantic split NB/NS at 2010 shares |
| Milk sold per dairy cow | 32-10-0113 | August to July, or the latest twelve months |
| Layers | 32-10-0121 | August-to-July average, or the latest twelve months |
| Veal calves | 32-10-0125 (Canada), split by the 1999 provincial shares | calendar year |
| Sheep | 32-10-0129 | July 1 and January 1 |

**Cattle on feed from slaughter.** Fed cattle marketed = cattle slaughter + the live exports that go to US slaughter,
July to June, by province (StatCan 32-10-0139), × the steer and heifer share of slaughter.
- **Exports:** only the share going to US slaughter counts; feeder and breeding cattle sent south are finished
  there. The share is from USDA ERS monthly US imports from Canada by class: 59% in 2013-14 (a heavy feeder-export
  year), 78% in 2025-26.
- **Steer and heifer share:** West 85.8%, East 82.6% (AAFC federally inspected slaughter, January 1 to June 7, 2025:
  West 984,649 steers and heifers and 163,245 cows and bulls; East 254,965 and 53,634), moved each crop year by the
  national share in AAFC's weekly federally inspected slaughter against the same period.
- **Days on feed** = average cattle on feeding operations × 365 ÷ fed cattle marketed, averaged over 2012-13 to
  2025-26: **172 days in the West**, 141 in the East (160 and 133 before feeder exports were taken out). That gives
  1.44 t of barley per fed animal in the West. The level matches StatCan's
inventories; the year-to-year change follows marketings. When a crop year's marketings aren't out yet, last year's
are moved by the July 1 cattle on feeding operations, province by province. For 2026-27 that is +15% in the West:
Alberta reported 900k head on feeding operations at July 1, 2026, against 777k a year earlier.

For the current crop year, January inventories aren't out yet (July only). Hogs and poultry run to AAFC's latest weekly
slaughter (late September 2026 in this build); milk and layers to the latest StatCan month.

## Finishing weights

Grain per fed animal moves with carcass weight (StatCan average cold dressed weight: 32-10-0125 cattle,
32-10-0126 hogs), in proportion: heavier finished animals eat more.

- **Feedlot cattle:** against the average over the model's years, since days on feed are calibrated on that
  average. Carcasses rose from 367 kg (2014) to 410 kg (2025); 2025-26 and 2026-27 are 6% above the average.
- **Finishing pigs:** against 2025, the year of the Manitoba hog guide the rates come from. Carcasses rose from
  94 kg (2012) to 106 kg (2025), so earlier years are up to 11% lower.
- A crop year uses the average of its two calendar years where published, else the latest year.
- Proportional scaling is conservative: the extra weight is put on at the end of the feeding period, when
  feed per kg of gain is highest.

## Hay shortfall

When hay is short, cows and backgrounders are fed grain instead. Western tame hay (32-10-0359, MB, SK, AB, BC) per
beef cow averages 3.9 t; in 2021-22 it fell to 2.2 t.

- Extra grain = (average hay per cow − this year's) × beef cows × **0.19 t of barley per t of hay short**, at the
  western cattle mix, reported as its own livestock group. Nothing is added when hay is at or above average.
- The rate is from Saskatchewan Agriculture's *Beef Cow Rations and Winter Feeding Guidelines* (2021): a 1,400 lb cow
  in mid-pregnancy eats 30 lb of alfalfa-grass hay a day, or 9 lb of hay + 18 lb of straw + 4 lb of barley. So 21 lb
  of hay is replaced by straw and 4 lb of barley (4/21 = 0.19). (Until 2026-10-06 the rate was fitted on StatCan's
  residual, 0.47.) A drought that also cuts straw would push the real rate higher.
- 2021-22 gets 1.1 Mt of extra grain; 2026-27, 0.4 Mt. **The 2026 hay crop isn't published yet**, so 2026-27
  repeats 2025's hay per cow (3.3 t, short); StatCan's November survey will replace it.
- Western only. The East's cow herd is small (0.4 million beef cows against 3.1 million).

## Crop quality: not added

A feed-grade supply index (the share of each crop grading feed) is the right fix for wheat in bad-quality years, but
no consistent series exists for 2012 onward:
- CGC's harvest-sample feed shares are published only from 2021 (degrading-factors pages; the 2024+ reports moved).
  The 2012–2020 reports give sample counts for the top grades only.
- CGC terminal receipts by grade: the low-grade share of western wheat falls from 10–19% to about 1% after 2020-21,
  a break in grading or handling, not better crops; durum misses 2016-17. Downgraded grain is fed at home and
  doesn't reach export terminals.
- Alberta's milling-against-feed wheat price spread breaks the same way: "other" wheat has been priced above milling
  wheat since 2021-22.

## Co-products

Distillers' grains (dry-grind ethanol) and corn gluten feed (wet milling) replace grain in rations. They are netted
out of each region's grain energy, in proportion across livestock classes, before step 2:

- **East:** Canada's corn for food and industrial use (32-10-0014), about 5.9 Mt, almost all in Ontario and Quebec.
- **West:** industrial use of wheat excluding durum (32-10-0013, prairie ethanol), 0.36 to 0.57 Mt.
- Each × **0.30 t of co-product per t of grain** (a 56 lb bushel of corn yields about 17 lb of distillers' grains) ×
  **1.0 barley-equivalent**. Distillers' grains have about corn's energy, but part of what is fed replaces protein
  meal rather than grain. A crop year not yet published repeats the latest.
- That takes about **1.8 Mt** of barley-equivalent off the East and 0.1 Mt off the West. The East's gap to StatCan
  (model minus StatCan) went from −2.4 Mt to −0.8 Mt on the 2012-13 to 2025-26 average.
- **Not netted out:** wheat millfeeds, bakery waste, and US distillers' grains imported into Canada (no data).

## Grain mix

- Each class keeps its 1999 provincial mix of barley, wheat, oats and corn, except western feedlot and backgrounding
  cattle: 85% barley, 8% wheat, 7% corn (the Manitoba guides feed barley; western finishing diets are typically over
  80% barley).
- The mix shifts each crop year with relative price and availability: share ∝ ration share × price index^−σ ×
  availability index^α. Price index = energy-adjusted crop-year price over its average (Alberta prices and US corn
  delivered to southern Alberta in the West, Ontario prices in the East; a crop year with no StatCan prices yet
  takes the latest month). Availability index = Canada supply (carry-in + production + imports) over its average; the
  current year uses last July's stocks, StatCan's latest production estimate and last year's imports.
- **σ = 1.0, an assumption.** No published substitution elasticity for Canadian feeding was found; 1 means a grain's
  share moves in inverse proportion to its relative energy-adjusted price. Fitting it on the farm survey gave no
  stable sign.
- **α ≈ 1.6, fitted each build on StatCan's farm survey** (32-10-0015: grain fed on the farm that grew it, West,
  reported by farmers, not a residual): wheat, durum and oats against barley, with grain constants and σ fixed. It
  comes out at 1.5 to 1.7 for any σ from 0.5 to 1.5 (s.e. about 0.9). Applied in the West only: eastern feeders buy
  the local crop at Ontario prices, which already reflect its size.
- (Until 2026-10-06 both were fitted on StatCan's residual grain shares: σ = 0.52, α = 2.40.)
- **Western corn** is set to what the West has: the western corn crop (32-10-0359) + US corn imported into the
  provinces outside Ontario and Quebec (32-10-0014, customs-based), because the 1999 rations predate Manitoba's corn
  crop (about 0.5 Mt then, over 2 Mt now). Both are measured; stock changes are left out (it runs within 5 to 10% of
  StatCan's western disappearance). Years whose imports aren't out yet are projected from the corn share of energy
  in the last three years, shifted by price; US corn fills what the crop doesn't. The rest of the West's demand is split among barley, wheat, durum and oats.
- **Durum** is its own grain, because durum and wheat trade independently. The 1999 study reports wheat with durum
  included, so in the West each class's wheat is split at durum's median share of the wheat and durum fed on western
  farms in StatCan's farm survey (24.4% over 2012-13 to 2025-26; it was 11.9% from the residual's regional split). Durum then shifts on its own price and availability (Alberta durum, energy value 1.06 against
  wheat's 1.08). No durum is fed in the East.

## Corn by origin

- **West:** US corn = StatCan's international imports into the provinces outside Ontario and Quebec (32-10-0014;
  March scaled to the full year until August is out), capped at the West's corn fed. The rest is western-grown.
  When imports aren't published, western-grown corn = the western crop (32-10-0359) × the share of it fed in the
  West over the last three years (111%; it is above 100% because the corn balance also includes Atlantic corn and
  stock changes), and US corn fills the rest of the West's corn demand.
- **East:** Ontario and Quebec imports (Ontario reports only total imports, all from abroad) as a share of their
  crop plus imports, applied to corn fed in the East. Imported corn is assumed to be used like local corn, by feeders
  and ethanol plants alike. Years not yet published use the last three years' share.
- Western US imports ranged from 0.3 Mt (2013-14) to 5.8 Mt (2021-22, the drought); 2026-27 is projected at 1.5 Mt
  against a record 2.5 Mt western crop.

## What it shows

| Crop year | Model, Canada | West | East | StatCan residual | West (est. split) | East (est. split) |
|---|---|---|---|---|---|---|
| 2021-22 | 18.5 Mt | 10.3 | 8.2 | 22.1 | 15.3 | 6.7 |
| 2022-23 | 17.9 | 9.6 | 8.4 | 19.8 | 12.8 | 7.0 |
| 2023-24 | 18.1 | 10.0 | 8.1 | 20.1 | 14.3 | 5.7 |
| 2024-25 | 18.0 | 9.6 | 8.4 | 18.0 | 12.1 | 5.9 |
| 2025-26 | 18.3 | 9.8 | 8.5 | 20.7* | 14.0 | 6.7 |
| **2026-27** | **18.8** | **10.3** | **8.5** | not yet published | | |

\* corn estimated until StatCan publishes August.

- **Below StatCan, and less noisy.** From 2012-13 to 2025-26 the model averages 17.1 Mt and StatCan's residual
  19.6 Mt. The residual's standard deviation is 1.54 Mt against the model's 1.10 Mt. The residual's extra swings
  are waste, dockage and balancing error.
- **Most of the gap is western.** The West's model averages 9.1 Mt against a residual of 12.6 Mt (barley within
  0.3 Mt on average; wheat about 2.1 Mt short). The residual carries
  dockage and waste, and the 1999 rations miss the feed wheat feedlots and hog barns buy (on-farm wheat feed in
  StatCan's survey is only 0.3 to 1.2 Mt against 2.6 to 3.4 Mt in the residual). The East's model averages 7.8 Mt
  against 7.0 Mt, after netting out co-products.
- **The year-to-year moves track StatCan's farm survey.** Correlation of y/y changes in western feed, model against
  on-farm feed: barley +0.79, wheat +0.68, durum +0.34, oats +0.70.
- **2026-27 by grain:** corn 11.1 Mt (Canadian-grown 9.2, US 1.9; West 4.2, East 6.9), barley 5.5, wheat
  (ex-durum) 1.6, durum 0.3 (all in the West), oats 0.3. By livestock (barley equivalent, before co-products):
  hogs 33%, feedlot and backgrounding cattle 26%, dairy 18%, poultry 17%, cow herd 4%, hay shortfall 2%. The West
  is up 6% on last year, mostly from more cattle on feed.

## Uncertainty range

The model is rerun with each judgment input at a low and a high value, the others at base (one at a time). Each
grain's range, for every crop year and region, is base − √(Σ falls²) to base + √(Σ rises²), treating the inputs as
independent. 2026-27, Canada (kt):

| Input | Low to high | Total | Barley | Wheat | Durum | Corn, Canadian | Corn, US |
|---|---|---|---|---|---|---|---|
| Price elasticity (assumption) | 0.5 to 1.5 | ±1 | +41 / −34 | −18 / +18 | −34 / +38 | ±3 | +24 / −35 |
| Availability elasticity (± 1 s.e. of the farm-survey fit) | 0.72 to 2.48 | ±6 | ±6 | ±18 | ±7 | – | – |
| Grain per t of hay short | 0 to 0.71 | −365 / +999 | −339 / +917 | −38 / +99 | −15 / +39 | – | +33 / −71 |
| Co-product yield (± 20%) | 0.24 to 0.36 | ±351 | ±44 | ±36 | ±1 | ±256 | ±9 |
| Durum share (farm-survey middle half) | 17.9% to 29.1% | ±2 | ±2 | +67 / −48 | −78 / +56 | – | +8 / −6 |
| US corn basis + freight (assumption) | US$1.20 to 2.00 | ±1 | ±7 | ±1 | – | – | ±8 |
| **Combined range** | | **18.3–19.9 Mt** | **5.14–6.41 Mt** | **1.54–1.74 Mt** | **206–371 kt** | **8.97–9.48 Mt** | **1.81–1.94 Mt** |

- **The hay rule dominates** barley and the western total: 0 (straw covers it all) to 0.71 t (grain replaces all of
  the hay's energy, Saskatchewan guide feed values: hay 60.0% TDN at 87.4% dry matter, barley 83.1% at 88.5%).
  It is widest in drought years (2021-22).
- **Co-products** set most of the range on eastern corn and the eastern total.
- **Prices hardly move the totals;** they shift tens of kilotonnes between grains.
- **Not in the range:** feeding rates, animal numbers and prices (published data), and the western wheat gap (feed
  wheat bought off the farm), which the model doesn't capture.

## Limits

- **Co-products are netted out at fixed rates** (0.30 t per t processed, barley's energy). Millfeeds, bakery waste
  and imported US distillers' grains are not.
- **The mix is the weakest part.** It does not track crop quality directly, so a year with a lot of feed-grade wheat
  shows up only through supply and price; western wheat is likely understated (model about 0.8 Mt against a residual near 3 Mt,
  some of which is dockage).
- **The rates are from 1999,** updated for the classes above. A new StatCan study or provincial ration surveys would
  replace them.
- **No backtest:** there is no independent measure of feed actually fed to score the model against. StatCan's
  residual is shown next to it, not as a target.

# Part 2: StatCan's residual (comparison only)

StatCan derives feed use as a residual of its supply and disposition balance, so it also absorbs waste, dockage
and measurement error. It is published cumulatively for Aug–Dec, Aug–Mar and the full crop year, and **for Canada
only**. The page shows it next to the model for every crop year, split by region:

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

### The old share regression (no longer used)

Until 2026-10-06 the grain-mix elasticities came from a pooled regression on StatCan's residual grain shares
(a = 2.40, b = −0.52), and the page also showed a forecast of StatCan's residual built on it. Both were dropped: the
model no longer takes any parameter from the residual. The regression is in git history (feed_data.fit_shares).

### Limits

- **The regional split is an estimate,** resting on the on-farm data and the production-share assumption for
  off-farm feed. Ontario feed mills buying western grain, or western feedlots buying eastern grain, would move it.
- **Crop quality** enters only through availability and price. CGC's harvest-sample grade distributions would add
  a direct measure; see TODO.md.
- **The energy values and the corn basis are assumptions.**
- **Seven test years is a small sample.** Re-check the elasticities each season.
