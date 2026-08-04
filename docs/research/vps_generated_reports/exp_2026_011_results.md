# EXP-2026-011 — Market Reaction Latency Results

_generated 2026-06-23 20:59 UTC_

Implements `EXP_2026_011_MARKET_REACTION_LATENCY_AUDIT.md` (LOCKED). Measurement only; no trading change. Forward-collected `first_seen_at` only (since 2026-06-09 14:29 UTC).

lag = (first Kalshi reprice onset after official_ts) minus first_seen_at. POSITIVE lag = market repriced AFTER WeatherBot first saw the event (potential edge). Reprice = |center move| >= 0.1 F. Polling is interval-censored: onset timing is an upper bound bounded by snapshot cadence.

Candidate gate (per channel): median lag >= 2 min AND positive-lag fraction >= 60% AND >= 100 event-days AND >= 2 stations.

## Channel: metar  (sources: metar, metar_lowlat; anchor=official; forward-latency cap 60 min)

- overall: events=43173 event-days=299 stations=20 median_lag=-10.56min pos_lag_frac=28%
- diagnostics: {'events': 96863, 'stale_backfill_excluded': 16785, 'no_onset_censored': 29461, 'no_market_series': 7444}
  - KATL: events=2466 event-days=15 stations=1 median_lag=-12.12min pos_lag_frac=25%
  - KAUS: events=2299 event-days=15 stations=1 median_lag=-7.44min pos_lag_frac=33%
  - KBOS: events=2029 event-days=15 stations=1 median_lag=-11.77min pos_lag_frac=27%
  - KDCA: events=2308 event-days=15 stations=1 median_lag=-10.09min pos_lag_frac=28%
  - KDEN: events=2456 event-days=15 stations=1 median_lag=-12.27min pos_lag_frac=24%
  - KDFW: events=2286 event-days=15 stations=1 median_lag=-10.36min pos_lag_frac=28%
  - KHOU: events=2283 event-days=15 stations=1 median_lag=-8.45min pos_lag_frac=31%
  - KLAS: events=1463 event-days=15 stations=1 median_lag=-4.57min pos_lag_frac=42%
  - KLAX: events=2159 event-days=15 stations=1 median_lag=-11.56min pos_lag_frac=26%
  - KMDW: events=2424 event-days=15 stations=1 median_lag=-12.28min pos_lag_frac=24%
  - KMIA: events=2021 event-days=15 stations=1 median_lag=-10.25min pos_lag_frac=29%
  - KMSP: events=2223 event-days=15 stations=1 median_lag=-8.03min pos_lag_frac=34%
  - KMSY: events=2029 event-days=15 stations=1 median_lag=-7.38min pos_lag_frac=34%
  - KNYC: events=232 event-days=14 stations=1 median_lag=+18.91min pos_lag_frac=97%
  - KOKC: events=2595 event-days=15 stations=1 median_lag=-13.12min pos_lag_frac=22%
  - KPHL: events=2294 event-days=15 stations=1 median_lag=-11.19min pos_lag_frac=25%
  - KPHX: events=2107 event-days=15 stations=1 median_lag=-9.51min pos_lag_frac=31%
  - KSAT: events=2455 event-days=15 stations=1 median_lag=-8.96min pos_lag_frac=32%
  - KSEA: events=2612 event-days=15 stations=1 median_lag=-13.32min pos_lag_frac=23%
  - KSFO: events=2432 event-days=15 stations=1 median_lag=-15.13min pos_lag_frac=18%
- **channel verdict: no candidate**

## Channel: model_run  (sources: nbm_run, hrrr_run, gfs_run, ecmwf_run; anchor=first_seen_window; forward-latency cap 480 min)

- overall: events=6422 event-days=299 stations=20 median_lag=-16.25min pos_lag_frac=27%
- diagnostics: {'events': 11374, 'no_market_series': 1002, 'no_onset_censored': 3598, 'stale_backfill_excluded': 352}
  - KATL: events=350 event-days=15 stations=1 median_lag=-17.53min pos_lag_frac=25%
  - KAUS: events=339 event-days=15 stations=1 median_lag=-12.94min pos_lag_frac=34%
  - KBOS: events=307 event-days=15 stations=1 median_lag=-16.96min pos_lag_frac=29%
  - KDCA: events=324 event-days=15 stations=1 median_lag=-15.09min pos_lag_frac=26%
  - KDEN: events=341 event-days=15 stations=1 median_lag=-19.49min pos_lag_frac=19%
  - KDFW: events=327 event-days=15 stations=1 median_lag=-17.76min pos_lag_frac=25%
  - KHOU: events=317 event-days=15 stations=1 median_lag=-15.22min pos_lag_frac=26%
  - KLAS: events=321 event-days=15 stations=1 median_lag=-8.17min pos_lag_frac=38%
  - KLAX: events=283 event-days=15 stations=1 median_lag=-17.54min pos_lag_frac=22%
  - KMDW: events=330 event-days=15 stations=1 median_lag=-18.50min pos_lag_frac=19%
  - KMIA: events=293 event-days=15 stations=1 median_lag=-14.27min pos_lag_frac=29%
  - KMSP: events=326 event-days=15 stations=1 median_lag=-11.63min pos_lag_frac=32%
  - KMSY: events=309 event-days=15 stations=1 median_lag=-11.46min pos_lag_frac=36%
  - KNYC: events=315 event-days=14 stations=1 median_lag=-16.39min pos_lag_frac=27%
  - KOKC: events=347 event-days=15 stations=1 median_lag=-20.30min pos_lag_frac=21%
  - KPHL: events=326 event-days=15 stations=1 median_lag=-18.38min pos_lag_frac=24%
  - KPHX: events=280 event-days=15 stations=1 median_lag=-10.76min pos_lag_frac=36%
  - KSAT: events=344 event-days=15 stations=1 median_lag=-12.10min pos_lag_frac=32%
  - KSEA: events=341 event-days=15 stations=1 median_lag=-17.10min pos_lag_frac=27%
  - KSFO: events=302 event-days=15 stations=1 median_lag=-21.64min pos_lag_frac=18%
- **channel verdict: no candidate**

## Channel: cli  (sources: cli, dsm; anchor=official; forward-latency cap 360 min)

- overall: events=86 event-days=86 stations=14 median_lag=-280.24min pos_lag_frac=0%
- diagnostics: {'events': 330, 'stale_backfill_excluded': 198, 'no_onset_censored': 46}
  - KATL: events=9 event-days=9 stations=1 median_lag=-269.91min pos_lag_frac=0%
  - KAUS: events=1 event-days=1 stations=1 median_lag=-335.53min pos_lag_frac=0%
  - KBOS: events=1 event-days=1 stations=1 median_lag=-58.40min pos_lag_frac=0%
  - KDEN: events=10 event-days=10 stations=1 median_lag=-329.90min pos_lag_frac=0%
  - KHOU: events=1 event-days=1 stations=1 median_lag=-317.30min pos_lag_frac=0%
  - KLAS: events=10 event-days=10 stations=1 median_lag=-276.84min pos_lag_frac=0%
  - KLAX: events=10 event-days=10 stations=1 median_lag=-250.29min pos_lag_frac=0%
  - KMDW: events=1 event-days=1 stations=1 median_lag=-246.95min pos_lag_frac=0%
  - KMIA: events=9 event-days=9 stations=1 median_lag=-281.30min pos_lag_frac=0%
  - KMSY: events=5 event-days=5 stations=1 median_lag=-311.55min pos_lag_frac=0%
  - KPHX: events=5 event-days=5 stations=1 median_lag=-283.88min pos_lag_frac=0%
  - KSAT: events=1 event-days=1 stations=1 median_lag=-276.87min pos_lag_frac=0%
  - KSEA: events=13 event-days=13 stations=1 median_lag=-286.72min pos_lag_frac=0%
  - KSFO: events=10 event-days=10 stations=1 median_lag=-267.86min pos_lag_frac=0%
- **channel verdict: insufficient sample**

## Channel: cross_venue (Polymarket lead) — channel 4

Same-station map (amendment A2, expanded 2026-06-09 after rules verification — see `EXP_2026_011_CROSS_VENUE_MAP_VERIFICATION.md`): comparable = KATL, KMIA, KAUS, KSEA, KLAX, KHOU, KSFO; excluded = KNYC, KMDW, KDEN, KDFW; all others excluded until verified.

Locked statistic: fresh divergence episode at PM observation t0 when |poly_center − kalshi_center| >= 0.5 F (re-arm band 0.25 F / sign flip; no prior paired PM obs within 15 min = left-censored, excluded from primary). Onset = first DIRECTIONAL Kalshi center move >= 0.1 F toward the PM side vs the t0−30min baseline, searched in (t0−30m, t0+60m]. lag = onset − t0; onset before t0 = already priced (negative). PM centers re-binned to the Kalshi ladder (support overlap >= 80%); Kalshi series from the WS top-of-book stream (exchange ts, skew-checked) with polling fallback. PM observations from the A7 WS collector (t0 = genuine receipt) where available, else polled (~2 min cadence, censored); diagnostics report which path each station-date used.

- overall (scored episodes): events=14 event-days=14 stations=5 median_lag=-18.22min pos_lag_frac=29%
- diagnostics: {'station_dates': 105, 'pm_observations': 368306, 'pm_polled_fallback_station_dates': 6, 'pm_ws_station_dates': 98, 'pm_support_overlap_skipped': 1390, 'episodes_left_censored': 121, 'episodes_scored': 14, 'episodes_no_follow': 11, 'episodes_gap_reduced': 18, 'episodes_poly_warmer': 20, 'episodes_poly_colder': 126}
  - KATL: events=5 event-days=5 stations=1 median_lag=-20.11min pos_lag_frac=20%
  - KHOU: events=3 event-days=3 stations=1 median_lag=-23.02min pos_lag_frac=0%
  - KLAX: events=1 event-days=1 stations=1 median_lag=+7.54min pos_lag_frac=100%
  - KSEA: events=3 event-days=3 stations=1 median_lag=-27.42min pos_lag_frac=0%
  - KSFO: events=2 event-days=2 stations=1 median_lag=+30.43min pos_lag_frac=100%
- episode |gap0|: median 1.04 F, max 3.81 F
- **channel verdict: insufficient sample**

## DSM channel

DSM is not first-class instrumented (no durable live table). Reported as not-yet-forward-instrumented per the handoff.

## Audit status

No channel meets the candidate gate yet. If event-days are below the threshold this is a forward-collection-in-progress run, not a closure. The latency axis is only closed once the committed forward window is reached with no candidate.
