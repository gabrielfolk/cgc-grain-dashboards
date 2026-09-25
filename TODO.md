# TODO

- [ ] **Automate the weekly refresh.** CGC publishes on Thursdays. Schedule a job to run the four refresh scripts
  (see README) and republish the five dashboard pages.
- [ ] **Add more crop deep dives:** peas, lentils, oats. Each needs one entry in `GRAINS` in `src/grain_data.py`,
  plus its URL in `LINKS` after publishing.
- [ ] **Re-test the canola forecast with real prices.** Get ICE canola futures and canola oil and meal prices
  (for example from Barchart), then re-test the price features in `src/canola_forecast.py`. The free soybean-complex
  proxies made forecasts worse.
- [ ] **Extend the forecast to wheat and durum exports,** using StatCan production estimates the same way as canola.
- [ ] **Add more supply data to the forecast:** StatCan's September canola estimate and on-farm stocks (table 32-10-0007).
- [ ] **Re-score the forecast each season.** The backtest has only eight test years.
