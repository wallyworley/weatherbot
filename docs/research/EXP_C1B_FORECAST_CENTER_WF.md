# EXP-C1b — Walk-Forward Conditioned Forecast Centers (results)

**Date:** 2026-06-07
**Experiment:** EXP-2026-008 / EXP-C1b. Implements the LOCKED pre-registration
[`EXP_C1B_PREREGISTRATION.md`](EXP_C1B_PREREGISTRATION.md).
**Run:** on the VPS against the local Postgres (no SSH-tunnel latency); 38 s, exit 0.
**Headline:** **No conditioned center passes.** Every bias-corrected / dynamically-weighted /
regime / obs-anchored center still **loses to the market**, and **none significantly beats
NBM-only**. This is the charter's final OOS center test → **the pre-committed decision rule
(prereg §6) fires: recommend the observation-only pivot.**

---

## 1. What was run

Six fixed candidate centers (locked prereg §2), all keeping NBM's spread and swapping only
the center, scored market-relative on the canonical coherent-snapshot benchmark,
**walk-forward** (trailing stats from `valid_date < station_local_date(as_of)`, 30 d, min 8),
pre-calibrator, with **Bonferroni-6** (z=2.638) paired CIs. Harness:
`research/center_market_benchmark_wf.py`.

### Execution note — a lead-1 alignment bug, found and fixed on the VPS

The first VPS run produced absurd `gfs_bc`/`ecmwf_bc` numbers (CRPS ≈ +2.4 °F). Cause: the
trailing forecast was reconstructed at "same local time on the historical day," which for
**lead-1** is a *same-day* (lead-0) forecast of that day — a more-accurate forecast than the
lead-1 one it was meant to bias-correct. Fixed by **lead-aligning** the reconstruction
(cutoff = `(hist_date − lead)` at the event's local time; lead-0 unchanged). Re-run on the
VPS; numbers below are the corrected, lead-aligned run. (Lead-0 results were unaffected by
the fix.) This refines the prereg §3 "same local time" wording to "same lead and local time."

## 2. Results (correction-applied subset; Bonferroni-6 CIs; positive = market/NBM wins)

### Lead 1

| center | n | dBrier_vs_mkt (CI) | dRPS_vs_mkt (CI) | vs_nbm ΔBrier (CI) | PASS |
|---|---:|---|---|---|:--:|
| nbm_only (base) | 283 | +0.0226 [+0.0114,+0.0338] | +0.0332 [+0.0186,+0.0478] | — | — |
| gfs_bc | 122 | +0.0287 [+0.0098,+0.0475] | +0.0611 | +0.0057 [−0.0136,+0.0251] | no |
| ecmwf_bc | 95* | +0.0237 [+0.0032,+0.0442] | +0.0492 | +0.0053 [−0.0183,+0.0290] | no |
| invmae_blend_bc | 122 | +0.0159 [+0.0001,+0.0317] | +0.0309 | −0.0070 [−0.0193,+0.0052] | no |
| regime_agree | 122 | +0.0239 [+0.0059,+0.0419] | +0.0426 | +0.0009 [−0.0088,+0.0107] | no |

### Lead 0

| center | n | dBrier_vs_mkt (CI) | dRPS_vs_mkt | vs_nbm ΔBrier (CI) | PASS |
|---|---:|---|---|---|:--:|
| nbm_only (base) | 302 | +0.1051 [+0.0796,+0.1306] | +0.1053 | — | — |
| gfs_bc | 156 | +0.1071 [+0.0655,+0.1487] | +0.1160 | +0.0093 [−0.0175,+0.0361] | no |
| ecmwf_bc | 131 | +0.1142 [+0.0645,+0.1638] | +0.1281 | +0.0266 [−0.0085,+0.0617] | no |
| hrrr_bc | 184 | +0.1256 [+0.0884,+0.1628] | +0.1335 | +0.0190 [−0.0114,+0.0494] | no |
| invmae_blend_bc | 156 | +0.0949 [+0.0557,+0.1340] | +0.0958 | −0.0030 [−0.0213,+0.0154] | no |
| regime_agree | 156 | +0.0903 [+0.0512,+0.1294] | +0.0898 | −0.0076 [−0.0208,+0.0056] | no |
| obs_anchor_l0 | 302 | +0.0930 [+0.0673,+0.1187] | +0.0941 | −0.0121 [−0.0307,+0.0065] | no |

\* `ecmwf_bc` lead-1 n=95 (<100) → labeled **inconclusive/data-limited** per prereg §6; it
loses to market anyway. All other variants have ≥122 (lead-1) / ≥131 (lead-0) applied events.

## 3. Exact conclusion

**No variant passes** the locked criteria. Specifically:

1. **Every variant loses to the market** at both leads — all `dBrier_vs_mkt` and `dRPS_vs_mkt`
   are positive with CIs excluding 0 in the wrong direction.
2. **None significantly beats NBM-only.** The only variants with a *point estimate* below NBM
   are `invmae_blend_bc` (lead-1 ΔBrier −0.0070) and `obs_anchor_l0` (lead-0 ΔBrier −0.0121) —
   both with CIs that **include 0** (not significant), and both still lose to the market.
3. **Bias-correcting the deterministic centers does not help** (gfs_bc/ecmwf_bc/hrrr_bc ≈ NBM
   or worse). The decorrelation blend and regime gate are ≈ NBM. Obs-anchoring lead-0 is ≈ NBM.

So conditioning, bias-correction, dynamic weighting, regime gating, and observation anchoring
— the harder, overfitting-prone mechanisms held back for the final test — **do not close the
market gap.** This completes the chain: benchmark audit (confirmed) → B1–B3 (mechanical fixes
are damage-reduction at most) → C1 first pass (no parameter-free center beats market) → **C1b
(no conditioned center beats market or reliably beats NBM).**

## 4. Decision (pre-committed, prereg §6)

No variant passed → **recommend converting WeatherBot to observation-only analytics**
(charter §7). The market-implied forecast is not beaten by any center WeatherBot can build
from available data. The *formal* kill threshold remains 500 fresh station-days or
2026-09-04; this walk-forward result on the existing window is strong OOS evidence to pivot
now rather than wait, but the calendar backstop and the final call are the operator's.

**No production change was made.** Paper mode remains; nothing was promoted.

## 5. Statistical limitations

- Existing ~6-week window; walk-forward OOS *estimate*, not the charter's fresh-station-day
  threshold. `ecmwf_bc` lead-1 is data-limited (n=95).
- Pre-calibrator; centers reuse NBM's spread (isolates the center question, consistent with C1).
- Deterministic centers use a trailing-30d bias (now lead-aligned); a longer history could
  sharpen the bias estimate, but the point estimates are ≈ NBM, not trending toward beating
  the market.
- `station_bias` has only NBM rows, so the *production* deterministic centers remain raw; this
  experiment's bias-correction is the research test of whether correcting them would help (it
  does not).

## 6. Overfitting risk

Low for the negative conclusion: these are mostly NBM-dominated or ≈NBM variants, evaluated at
a Bonferroni-6 bar, and none even reaches significance vs NBM (let alone the market). There is
no positive result to overfit.

## 7. Recommended next step

Adopt the pivot (or set the calendar backstop). If continuing the forecast program at all, the
only remaining avenues outside this fixed-variant test are genuinely new *information* (e.g., a
materially better NWP source or mesoscale/obs feature not in the current data) — which the
charter's "do not add new production models" rule blocks without a fresh pre-registration and
which the consistent negative across A→C1b makes a poor bet. Trading research stays blocked
(no forecast edge). See `EXPERIMENT_PLAN_NEXT.md` for the updated state.
