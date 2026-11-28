# TODO

- [ ] **Each October: add the MLB season.** After the regular season ends, run `.venv/bin/python src/mlb.py`, then
  `src/grain_data.py` and `src/build_site.py` (mustard page, Exports to the United States).
- [ ] **January 2027: add the 2027 Crop Planning Guide.** When Saskatchewan Agriculture publishes it, add its format
  id to `FORMAT_IDS` in `src/crop_guide.py` (Publications Centre archive, product 122661), run
  `.venv/bin/python src/crop_guide.py`, then `src/margins_data.py` and `src/build_site.py`.

### Other

- [ ] **Download only new StatCan data (maybe).** Each scheduled refresh re-downloads all 19 cached StatCan tables
  (~130 MB of its ~140 MB), three runs a week, to catch a few releases a month. StatCan's Web Data Service
  `getCubeMetadata` returns each table's `releaseTime`; `src/external.py --refresh` could fetch only tables released
  since the cached copy (most weeks none, ~10 MB a run). Kept as is for now (2026-11-24): simple, and it can't
  miss a release.
- [ ] **Share the chart code between pages.** The overview, margins and crop template each carry their own copy of
  the chart helpers; move them into one shared script if more pages are added.
- [ ] **Ports & Logistics dashboard (deferred).** Sources: Quorum's weekly Grain Monitoring Program PDF (Vancouver and
  Prince Rupert vessel lineup, arrivals, clearances, inbound; rail car unloads by port; out-of-car time; terminal
  capacity used), Quorum's monthly data tables (vessel time in port, rail volumes, car cycles and transit times since
  2013-14), and USDA's Gulf and PNW vessel lineups for US context. Check Quorum's terms before republishing. Only the
  current crop year's weekly PDFs stay online, so start archiving them early if weekly history is wanted.
- [ ] **Re-test the canola forecast with real prices** (deferred 2026-11-24). Re-test the price features in
  `src/canola_forecast.py` with ICE canola futures; the free soybean-complex proxies made forecasts worse. Sources
  checked 2026-11-24: Yahoo RS=F still empty; Barchart free gives only 2 years of daily history; Investing.com has no
  API and bars redistribution; Manitoba's weekly price PDFs (canola cash, meal) only go back to about 2021. Cheapest
  workable route: finzdata.com, ICE canola daily bars since 2007 for $49 one-time, plus a free API key (last 30 days)
  for the weekly refresh. Its licence says "for your own research and trading; not for resale", so ask
  info@finzdata.com before publishing price-based forecasts, keep raw prices out of the repo, and spot-check it
  against known settlements (it began selling futures history in Oct 2026). No free canola oil or meal series found.
- [ ] **Extend the forecast to wheat and durum exports,** using StatCan production estimates the same way as canola.
- [ ] **Add more supply data to the forecast:** StatCan's September canola estimate and on-farm stocks (table 32-10-0007).
- [ ] **Re-score the forecast each season.** The backtest has only eight test years.
- [ ] **Decide on repo visibility.** The repo is public for now. To make the code private while keeping the site
  public, either upgrade to GitHub Pro and switch the repo to private (same site address), or use two repos: this one
  private, plus a small public repo that holds only the built `docs/` site. On GitHub Free, a private repo can't serve
  a public Pages site.
- [ ] **Consider a GitHub no-reply commit email** (Settings → Emails) so the personal email isn't shown in the
  public commit history.
- [ ] **Feed-model quality term** once a consistent feed-grade series exists. CGC's harvest-sample feed shares are
  published only from 2021 (2024+ under /grain-harvest-export-quality/); 2012–2020 reports give top-grade sample
  counts only. Terminal receipts by grade and the Alberta milling/feed wheat spread both break in 2020-21 (tried
  2026-10-06). Could start a 2021+ series and use it once there are enough years.
- [x] **Western feed-wheat price** (2026-11-26): switched from Alberta's "other" wheat (priced above milling since
  2021-22) to the Saskatchewan and Manitoba average, which stays below milling. Still worth asking StatCan what
  Alberta's series now covers.
- [ ] **Western wheat in feed rations** is still the model's biggest gap against StatCan (2.1 Mt a year on average).
  Look for a current Prairie hog and feedlot ration survey (Prairie Swine Centre, Alberta Pork, feed-mill data);
  the Manitoba government's example hog rations (undated) have no wheat in finisher feed.
- [ ] **Update the western hog and feedlot grain mix** from a published current diet survey. The 1999 Manitoba hog
  rations have no wheat; the 2025 Manitoba swine guide gives feed amounts but no ingredients, and no Prairie source
  with current grain shares was found (2026-10-06). This is the main suspect for western wheat being understated.
- [x] **Alberta check resolved** (2026-11-26): the Alberta beef life cycle study's rations give 1.76 t of barley per
  fed animal against the model's 1.72 t (2012-13), so the feedlot rates hold; Alberta Agriculture's 3.6 Mt is a
  top-down estimate the model is not calibrated to. Rations in `reference/alberta_beef_lca_rations.csv`.
- [x] **National Beef Sustainability Assessment read** (2026-11-26): calf-fed share 45% (2021), its western rations a
  second feedlot check (2.28 t per fed animal against the model's 1.82 t; mostly its 2.35%-of-body-weight intake) and
  the range ends for feedlot, cow and bull grain. Rations in `reference/nbsa_2021_rations.csv`. Peer-reviewed version
  (Aboagye et al. 2024, Canadian Journal of Animal Science) not read: the publisher blocks automated access.
- [ ] **Add AAFC's Outlook for Principal Field Crops** (feed, waste and dockage by crop) to the feed page as a second
  reference next to StatCan.
- [ ] **Net out millfeeds and imported US distillers' grains** if a source turns up. The NBSA's western rations carry
  10% distillers' grains in finishing and 19% in backgrounding (dry matter); compare with the model's domestic co-products.
