# EXP-C2 (Lead-0 Obs-Timing Nowcast) Pre-Registration

**Date:** 2026-06-08
**Status:** LOCKED / APPROVED 2026-06-08. Window amendment applied (afternoon hours 13 to 17,
fresh-METAR 10 min); building to spec.
**Experiment:** EXP-2026-010 (concrete instance of EXP-2026-006, scoped to the lead-0
observation-timing mechanism surfaced by the EXP-2026-009 forensics dataset).
**Constraint:** research-only, paper-only, no production trading change. Promotion still
requires `WEATHERBOT_PROMOTION_CRITERIA.md`.

> Locked once approved. No cohort or signal may be added, removed, or re-specified after
> sign-off. All variants are reported including failures. Changes require a new
> pre-registration.

---

## 1. Hypothesis (the single claim)

In a pre-registered lead-0 cohort (afternoon, fresh observation), a forecast distribution
anchored on the live observation (the day's high so far plus a climatological remaining-rise
distribution) beats the Kalshi market-implied distribution out of sample on market-relative
Brier AND RPS.

This is the one remaining edge-adjacent idea after the edge investigation closed: at lead-0
the market is sharp partly because it reacts to realized observations, and WeatherBot sees the
same observation. The claim is narrow and conditional, not "WeatherBot beats the market."

## 2. Why this and not something already falsified

EXP-C1b already showed an obs-anchor center, scored across ALL lead-0, does NOT beat the
market (it lost by +0.093 Brier; vs-NBM point estimate not significant). So the only honest
remaining version is CONDITIONAL: a specific, signal-time-observable cohort where the
obs-anchored distribution beats the market. The EXP-2026-009 forensics dataset exists to test
exactly this, cheaply.

**Honest prior: this is a long shot.** The market likely reprices observations within
seconds via faster feeds. This test measures only forecast information (does the obs-anchored
distribution beat the market in the cohort), not latency or execution. A pass would be a
forecast-information candidate, not yet a tradable edge.

## 3. The single signal (locked)

`obs_anchor_dist`: at a lead-0 snapshot, build a bucket distribution as
`final_high = metar_max_so_far + R`, where `R` is the walk-forward remaining-rise
distribution for that station and local-hour bucket (Normal with mean and std estimated from
strictly-prior days; std added to the EXP-2026-009 climo, which currently stores only
mean/p50). Bucket probabilities are `P(metar_max + R in [lower, upper))`, normalized over the
same captured bucket set, then scored against settlement and against the market mid at that
snapshot. NBM shape is not used; the spread comes from the remaining-rise climatology.

No other signal is tested. The future market move (`market_moves_after_recent_metar`) is a
descriptive label only and is NOT a feature (using it would be look-ahead).

## 4. The single primary cohort (locked)

Primary pass cohort = lead-0 snapshots that satisfy ALL of:
1. `lead_day == 0`,
2. local hour in [13, 17] (afternoon, near and after peak heating, where remaining-rise is
   small and well-estimated; 18 dropped because the high is essentially set and the market is
   already resolved, leaving no room for edge),
3. `latest_metar_age_min <= 10` (a fresh observation is present).

Reported secondary cuts (confirmatory only, NOT additional pass cohorts, to avoid
slice-mining): within the primary cohort, split by `live_metar_boundary_distance_f <= 0.5F`
vs `> 0.5F`. These are reported with Bonferroni-adjusted CIs but a pass is judged on the
primary cohort.

## 5. Data and walk-forward / OOS protocol

- Source: the EXP-2026-009 forensics CSV (per-snapshot market probs, bucket set, live METAR,
  remaining-rise climo, settlement), plus a climo-std extension.
- All features as-of the snapshot. Remaining-rise climatology uses strictly-prior days only.
  Settlement is for scoring only. Current valid date excluded.
- **OOS is a chronological held-out split:** any free parameter (the remaining-rise climo, the
  Normal model) is frozen on the earlier 60% of dates and the result is evaluated ONLY on the
  later 40% of dates, plus fresh days accumulating after this registration. Design choices are
  never made on the evaluation portion.

## 6. Metrics

Market-relative Brier and RPS in the cohort (paired per-event deltas vs the market), with
paired 95% CIs; CRPS and center MAE reported as secondary. Report n (cohort station-snapshots)
and station count everywhere.

## 7. Pass criteria (tight)

The signal PASSES only if ALL hold on the held-out evaluation portion of the PRIMARY cohort:
1. Negative market-relative Brier AND negative market-relative RPS, each with a paired CI
   excluding 0.
2. >= 100 cohort station-snapshots and >= 2 stations.
3. The negative point estimate holds in >= 2 stations and >= 2 chronological sub-splits (not a
   single slice).
4. No leakage.

Beating NBM is not sufficient; it must beat the MARKET. A result that ties the market
(CI includes 0) is not a pass.

## 8. Decision rule (pre-committed)

- **Pass:** a lead-0 forecast-information candidate. It then requires the full
  `WEATHERBOT_PROMOTION_CRITERIA.md` path (production-like re-score, fresh OOS station-days,
  realistic fills) before any trading change. Report only; no production change.
- **No pass:** this was the last edge-adjacent avenue. Combined with audit + B1-B3 + C1/C1b,
  it definitively closes the forecast-edge question, and WeatherBot moves to observation-only
  analytics for good (charter §7). No production change either way.

## 9. Leakage controls

As-of features only (live METAR, climo from strictly-prior days); settlement used solely for
scoring; market mid from the same snapshot; no future market move as a feature; chronological
held-out so the cohort and signal are never fit on the evaluation data.

## 10. Overfitting controls

ONE primary cohort and ONE signal, locked at sign-off. No search over cohorts or signals.
Secondary boundary cut is confirmatory only with Bonferroni-adjusted CIs. Climo is
walk-forward / prior-only. All results reported including failures.

## 11. Known limitations (stated up front)

- ~30-day existing window; the chronological held-out is a strong OOS estimate but not the
  charter's fresh-station-day threshold.
- Forecast-information test only: it does not model latency or execution, so even a pass is not
  yet a tradable edge.
- KNYC has sparse METAR (avg age ~24 min); it will frequently fail the fresh-METAR condition
  and contribute little.
- Remaining-rise Normal model is an approximation; an empirical-quantile version is a possible
  amendment but is NOT added without re-registration.

## 12. What I will build after approval

A research-only scorer (extend `research/market_information_forensics.py` climo to add the
remaining-rise std, plus a small `research/nowcast_cohort_test.py`) implementing exactly
sections 3 to 7, run on the VPS. Results to `EXP_C2_NOWCAST_RESULTS.md` + registry
EXP-2026-010. No production code touched.

---

**Requested sign-off:** approve the signal (§3), the primary cohort (§4), the OOS protocol
(§5), and the pass criteria (§7), or amend, before I write any code.
