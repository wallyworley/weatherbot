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

→ Step 1 was run as EXP-B1 (see §9 below).

## 9. EXP-B1 experiment results (2026-06-06)

Harness: `research/floor_basis_experiment.py` (research-only). For each lead-0 TMAX
event it rebuilds the production distribution point-in-time (`as_of` = coherent
snapshot ts, no future data; production bias + HRRR/GFS center held fixed,
pre-calibrator) and re-scores the model under several floor policies against the
**same** market midpoints, using the canonical benchmark scoring. n = 289–290
events. Market Brier ≈ 0.0876 here reproduces the canonical benchmark's coherent
lead-0 market Brier (0.087) — a consistency check on the harness.

| policy | model Brier | dBrier vs mkt | dRPS vs mkt | dCRPS vs mkt | dCenterMAE | winner-zeroed | vs prod (Brier) |
|---|---:|---:|---:|---:|---:|---:|---:|
| **prod_hard** (current) | 0.1765 | +0.0889 | +0.0818 | +0.331 | +0.52 | 7 | +0.0000 |
| floor_off | 0.1940 | +0.1067 | +0.0857 | +0.157 | +0.23 | 5 | +0.0177 |
| **soft_w0.50** | **0.1710** | **+0.0837** | **+0.0701** | **+0.217** | **+0.36** | **1** | **−0.0052** |
| soft_w0.25 | 0.1763 | +0.0890 | +0.0721 | +0.182 | +0.30 | 1 | +0.0000 |
| minus_0.5 | 0.1758 | +0.0884 | +0.0769 | +0.229 | +0.38 | 5 | −0.0005 |
| minus_1.0 | 0.1829 | +0.0956 | +0.0807 | +0.190 | +0.31 | 6 | +0.0066 |
| minus_wf (p85≈0.60F) | 0.1793 | +0.0919 | +0.0806 | +0.241 | +0.38 | 8 | +0.0030 |

(Negative = model better than market / better than the production floor.)

**Findings:**

1. **The soft floor is the effective mechanism.** Capping the floor's injected
   confidence (`soft_w0.50` = 0.5·floored + 0.5·unfloored bucket probs) **improves
   on the production hard floor across every metric** — Brier 0.1765→0.1710, RPS
   gap +0.0818→+0.0701, CRPS +0.331→+0.217, center MAE +0.52→+0.36 — and cuts
   catastrophic **winner-zeroing 7→1**. `soft_w0.25` improves RPS/CRPS and is
   Brier-neutral. Both soft weights move the same direction, so this is not a
   knife-edge fit.
2. **δ-subtraction does not work.** Fixed (−0.5/−1.0 °F) and walk-forward
   (`minus_wf`, prior-day p85 ≈ 0.60 °F) buffers leave winner-zeroing at 5–8. The
   damaging over-reads are a 2–5 °F **fat tail** (e.g., KMDW 64.4 vs 59, KNYC 78.1
   vs 75); a uniform buffer can't catch them without destroying the floor on the
   80% of days it is correct.
3. **Don't just delete the floor.** `floor_off` *worsens* Brier/RPS (the floor's
   sharpening is net-valuable when correct) even though it helps CRPS/center. The
   soft floor keeps the upside while removing the catastrophic downside.
4. **Damage reduction, not edge.** Even the best policy leaves the market gap at
   **+0.0837** Brier — the market still wins decisively at lead 0. This is exactly
   what the audit predicted: the floor is a real wrong-direction defect, fixing it
   removes self-inflicted damage, but it does **not** create forecast-information
   advantage.

**Recommendation (updated):** The **soft floor** is the candidate fix; the
δ-subtraction and floor-off forms are rejected. **No production change yet** — the
EXP-B1 evaluation is in-sample to the historical window. Required before any
production change (`WEATHERBOT_PROMOTION_CRITERIA.md` §4):
- implement the soft floor behind a research-only flag (default = current hard floor);
- validate it **walk-forward on fresh lead-0 station-days** via the canonical
  coherent benchmark (Brier/RPS not degraded vs production, winner-zeroing reduced),
  **without** tuning the soft weight in-sample;
- confirm no leakage.

**Statistical limitations:** in-sample window (~6 weeks, lead-0 only); the soft
weight (0.50) is a fixed default, not validated OOS; winner-zeroing counts are small
(1–8) so their differences are directional, not precise.

**Overfitting risk:** Low–Medium. The soft floor has one obvious parameter (the
weight); keep it fixed and validate OOS rather than optimizing it on this window.
