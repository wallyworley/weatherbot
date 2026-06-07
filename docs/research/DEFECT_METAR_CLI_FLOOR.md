# Defect Report: METAR vs CLI Intraday-Floor Basis Mismatch

**Date:** 2026-06-06
**Defect class:** Mechanical / wrong-direction confidence manufacturing
**Severity:** Medium–High (lead-0 same-day TMAX only)
**Recommendation:** Demote the hard METAR floor to a CLI-consistent / soft floor
behind a research flag; do **not** ship a production change until the market-relative
benchmark confirms improvement out of sample.

---

## 1. Files and functions inspected

- `models/distribution.py`
  - `_intraday_bounds(station, target_date, now_utc)` — computes
    `MAX(temp_f)`/`MIN(temp_f)` from `metar_obs` for the station-local day up to `now`.
  - `build_station_distribution(...)` lines 515–526 — sets `cdf.floor = tmax_so_far`
    for same-day (`lead_day == 0`) `TMAX_DAILY`.
  - `PiecewiseCDF.cdf` / `prob_between` — the floor truncates `P(X < floor) = 0`
    and renormalizes the conditioned mass.
- `jobs/settle_paper_fills.py` (referenced) — settlement is CLI-based; truth ≠ METAR.
- Research diagnostic built for this report: `research/floor_basis_diagnostic.py`.

## 2. The defect

Same-day TMAX distributions are conditioned on a **persistence floor** equal to the
max METAR temperature observed so far: the model asserts `P(final TMAX < floor) = 0`.
Kalshi settles on the **NWS CLI daily maximum**. METAR peak temperatures and CLI
maxima are **different quantities** (5-minute/1-minute sensor peak capture,
°C→°F rounding, NWS QC). When the METAR floor lands **above** the eventual CLI
max, the conditioning zeroes the bucket that actually settles, producing a
confident-wrong distribution that diverges from the market in the losing direction.

## 3. Data used and sample size

- **Systematic basis** (full-day METAR max vs CLI max, last 120 days):
  - n = **880 station-days**
  - mean(METAR_max − CLI) = **+0.012 °F** (no net bias), sd = **0.68 °F**
  - METAR_max **> CLI**: **372 (42%)**; **> CLI + 0.5 °F**: **181 (21%)**
  - METAR_max **< CLI**: 301 (34%); within ±0.5 °F: 513 (58%)
- **Direct floor impact on the scored lead-0 benchmark events**
  (`research/floor_basis_diagnostic.py`, coherent snapshot, reconstructing the
  floor as `MAX(metar.temp_f)` up to each event's snapshot time):
  - events scored: **290**; all 290 had a reconstructable floor
  - floor **> CLI truth**: **58 (20.0%)**
  - floor **≥ winning-bucket upper edge** (winner fully truncated to ~0): **6 (2.1%)**
  - on those 6 zeroed-winner events: mean model prob on the winning bucket =
    **0.102** vs market = **0.797**

Representative zeroed-winner cases:

| station | date | floor °F | CLI truth °F | winning bucket | model P(win) | market P(win) |
|---|---|---|---|---|---|---|
| KATL | 2026-05-31 | 77.0 | 75.0 | [74,76) | 0.015 | 0.822 |
| KNYC | 2026-05-28 | 78.1 | 75.0 | (<76) | 0.067 | 0.874 |
| KNYC | 2026-04-25 | 51.1 | 50.0 | (<51) | 0.000 | 0.912 |
| KNYC | 2026-05-11 | 62.1 | 61.0 | (<62) | 0.082 | 0.936 |
| KMDW | 2026-05-13 | 64.4 | 59.0 | (<60) | 0.333 | 0.772 |

## 4. Metrics used

Mean absolute basis (METAR_max − CLI); count/percent of events where floor > truth
and where floor zeroes the winning bucket; normalized model vs market probability
on the (truncated) winning bucket.

## 5. Exact conclusion

The intraday floor uses the **wrong settlement basis**. On a near-unbiased mean
but with 0.68 °F of noise, the METAR floor sits **above** the CLI settlement on
**~20% of same-day events** and **fully zeroes the winning bucket on ~2%**,
where the model then prices the eventual winner at ~10% against a market at ~80%.
This is a genuine wrong-direction mechanical defect: it manufactures
**false divergence** from the market on same-day markets (the exact lead where the
benchmark shows WeatherBot's worst deficit), and any trade taken on that
divergence loses. The defect is **not the whole** lead-0 gap (the coherent-snapshot
benchmark shows WeatherBot losing broadly even excluding these events), but it is a
clear, fixable contributor.

## 6. Statistical limitations

- The 6 fully-zeroed events are a small absolute count; the per-event Brier impact
  is large but the population estimate (2.1%) has a wide interval.
- Floor reconstruction uses `MAX(metar.temp_f) ≤ snapshot_ts`, which matches the
  production query logic but assumes the same METAR rows were present live; minor
  late-arriving-observation differences are possible.
- The 120-day basis sample mixes seasons and stations; the over-read fraction is
  likely station- and regime-dependent (not separated here).

## 7. Overfitting risk

**Low for the diagnosis** (it is a basis/measurement fact, not a fitted model).
**Medium for any fix:** a tuned "floor − margin" buffer could be over-fit to the
recent over-read distribution. A fix must be validated on the market-relative
benchmark out of sample, not chosen to maximize in-sample Brier.

## 8. Recommended next step

1. Build a **research-only** alternate floor basis behind a config flag (no
   production default change), candidate forms:
   - CLI-consistent soft floor: cap the probability removed below the floor rather
     than hard-zeroing (e.g., leave a small residual mass), or
   - floor = `metar_max − δ` with δ chosen from the **prior** over-read
     distribution (walk-forward), or
   - drop the hard floor entirely for TMAX and rely on the HRRR/NBM center.
2. Score each candidate on `snapshot_market_benchmark.py` (Brier/RPS/CRPS/center
   MAE vs market), lead-0 only, out of sample.
3. Promote only if it **improves or neutralizes** market-relative scores with no
   leakage, per `WEATHERBOT_PROMOTION_CRITERIA.md` §4. Mechanical fixes do not
   require positive P&L but must not degrade market-relative skill.
