# EXP-C2 Lead-0 Obs-Timing Nowcast — Results

_generated 2026-06-08 23:24 UTC_

Implements `EXP_C2_NOWCAST_PREREGISTRATION.md` (LOCKED). Research-only; no trading change.
Negative dBrier / dRPS = the obs-anchored nowcast BEATS the market.

## Cohort
- lead-0, local hour [13,17], latest METAR age <= 10 min
- total cohort events: 8511 across 20 stations, 30 dates
- chronological cut date: 2026-05-27 (design < cut <= held-out)

## Primary result (market-relative, paired)

| split | result |
|---|---|
| design (earlier 60%) | n= 1881 st=19 dBrier=+0.0560 [+0.0527,+0.0592]  dRPS=+0.0487 [+0.0446,+0.0529] |
| **HELD-OUT (later 40%)** | **n= 6630 st=20 dBrier=+0.0455 [+0.0435,+0.0474]  dRPS=+0.0354 [+0.0338,+0.0370]** |
| overall | n= 8511 st=20 dBrier=+0.0478 [+0.0461,+0.0495]  dRPS=+0.0384 [+0.0368,+0.0399] |

### Held-out per-station

| station | result |
|---|---|
| KATL | n=  360 st= 1 dBrier=+0.0382 [+0.0292,+0.0471]  dRPS=+0.0357 [+0.0266,+0.0447] |
| KAUS | n=  360 st= 1 dBrier=+0.0376 [+0.0294,+0.0458]  dRPS=+0.0266 [+0.0214,+0.0319] |
| KBOS | n=  359 st= 1 dBrier=+0.0690 [+0.0620,+0.0759]  dRPS=+0.0740 [+0.0651,+0.0829] |
| KDCA | n=  360 st= 1 dBrier=+0.0285 [+0.0228,+0.0343]  dRPS=+0.0169 [+0.0130,+0.0207] |
| KDEN | n=  360 st= 1 dBrier=+0.0252 [+0.0165,+0.0339]  dRPS=+0.0201 [+0.0142,+0.0261] |
| KDFW | n=  360 st= 1 dBrier=+0.0256 [+0.0178,+0.0334]  dRPS=+0.0380 [+0.0291,+0.0469] |
| KHOU | n=   30 st= 1 dBrier=+0.0399 [+0.0303,+0.0495]  dRPS=+0.0207 [+0.0141,+0.0274] |
| KLAS | n=  360 st= 1 dBrier=+0.0210 [+0.0128,+0.0293]  dRPS=+0.0148 [+0.0091,+0.0204] |
| KLAX | n=  360 st= 1 dBrier=+0.1002 [+0.0902,+0.1102]  dRPS=+0.0614 [+0.0552,+0.0677] |
| KMDW | n=  360 st= 1 dBrier=+0.0635 [+0.0587,+0.0684]  dRPS=+0.0385 [+0.0351,+0.0418] |
| KMIA | n=  360 st= 1 dBrier=+0.0794 [+0.0677,+0.0911]  dRPS=+0.0499 [+0.0427,+0.0571] |
| KMSP | n=  359 st= 1 dBrier=+0.0441 [+0.0371,+0.0512]  dRPS=+0.0402 [+0.0331,+0.0474] |
| KMSY | n=  360 st= 1 dBrier=+0.0283 [+0.0215,+0.0351]  dRPS=+0.0153 [+0.0110,+0.0197] |
| KNYC | n=  122 st= 1 dBrier=+0.0763 [+0.0631,+0.0894]  dRPS=+0.0826 [+0.0620,+0.1032] |
| KOKC | n=  360 st= 1 dBrier=+0.0470 [+0.0389,+0.0551]  dRPS=+0.0465 [+0.0380,+0.0550] |
| KPHL | n=  360 st= 1 dBrier=+0.0438 [+0.0378,+0.0498]  dRPS=+0.0373 [+0.0318,+0.0429] |
| KPHX | n=  360 st= 1 dBrier=+0.0492 [+0.0407,+0.0576]  dRPS=+0.0299 [+0.0242,+0.0356] |
| KSAT | n=  360 st= 1 dBrier=+0.0046 [-0.0030,+0.0121]  dRPS=+0.0037 [-0.0024,+0.0098] |
| KSEA | n=  360 st= 1 dBrier=+0.0355 [+0.0274,+0.0436]  dRPS=+0.0296 [+0.0238,+0.0354] |
| KSFO | n=  360 st= 1 dBrier=+0.0678 [+0.0596,+0.0760]  dRPS=+0.0442 [+0.0387,+0.0498] |

### Held-out chronological sub-splits

- sub-A: n= 3303 st=19 dBrier=+0.0462 [+0.0435,+0.0489]  dRPS=+0.0351 [+0.0329,+0.0373]
- sub-B: n= 3327 st=20 dBrier=+0.0447 [+0.0420,+0.0475]  dRPS=+0.0357 [+0.0334,+0.0381]

### Secondary boundary cut (confirmatory only, held-out)

- boundary <= 0.5F: n= 2904 st=20 dBrier=+0.0488 [+0.0464,+0.0512]  dRPS=+0.0329 [+0.0311,+0.0347]
- boundary  > 0.5F: n= 3726 st=19 dBrier=+0.0429 [+0.0400,+0.0458]  dRPS=+0.0374 [+0.0349,+0.0398]

## Pass criteria (held-out primary cohort)

- [ ] brier_neg_ci
- [ ] rps_neg_ci
- [x] n>=100
- [x] stations>=2
- [ ] >=2 neg stations
- [ ] >=2 neg subsplits

## VERDICT: NO PASS

The obs-anchored nowcast does NOT beat the market in the pre-registered held-out cohort. Per the locked decision rule, this closes the last edge-adjacent avenue: WeatherBot moves to observation-only analytics. No production change.

---

## Provenance / methods note

- Run on the VPS PostgreSQL database (`research/nowcast_cohort_test.py`), scoring the
  persisted EXP-2026-009 forensics CSV (`market_information_forensics_2026-06-08.csv`,
  70,617 snapshots / 315 station-days / 20 stations).
- Cohort skips: 2,910 snapshots dropped for METAR age > 10 min, 159 for missing obs or
  settlement. 8,511 cohort events scored (6,630 held-out).
- Signal = `obs_anchor_dist`: `metar_max_so_far + Normal(remaining-rise mean, std)`, the
  remaining-rise climatology estimated from strictly-prior days only (60-day lookback,
  min 8 prior days, std floor 0.5F), normalized over the captured bucket set.
- Market distribution = the snapshot's normalized mid-price bucket probabilities.
- No leakage: every feature is as-of the snapshot; settlement used only to score; no future
  market move used as a feature; chronological held-out (design dates < 2026-05-27 <= eval).

## Interpretation

The result is not marginal. The market beats the obs-anchored nowcast by ~0.045 Brier and
~0.035 RPS in the held-out cohort, with tight CIs far from zero, and the sign is the same in
all 20 stations, both chronological sub-splits, and both boundary cuts. The near-boundary
slice (where a fresh observation should be most decisive) loses by slightly MORE, not less.
This is the cleanest possible refutation of the lead-0 obs-timing edge hypothesis: the market
has already priced the live observation that WeatherBot sees, and a climatological
remaining-rise model on top of that observation is strictly less informative than the market.

Combined with EXP-2026-001 (benchmark audit), EXP-B1 to B3 (floor/HRRR/GFS defects, all
damage-reduction only), and EXP-C1/C1b (no center variant beats the market out of sample),
this closes the forecast-edge question. WeatherBot moves to observation-only analytics per the
charter. No production trading change.
