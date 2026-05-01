"""Centralised help / legend text. The dashboard renders these as expandable
info panels and tooltip hints.

Keep these short and operational — describe what the user sees, what to
do when it goes red, and (where relevant) what an incident in this metric
looked like historically.
"""

OVERVIEW = """
**What this is.** A live read of the weather-bot's health, calibration, and
trading state. Refreshes every 15 seconds.

**The five-second test.** Glance at the colored tiles at the top of the
**Status** tab. If they're all green, nothing needs you. If anything is
amber or red, switch to the corresponding tab.

**Color rules.**
- 🟢 GREEN — healthy, within thresholds.
- 🟡 AMBER — degrading, watch it but no immediate action.
- 🔴 RED — out of bounds. **For MODEL/RISK/PNL, the trade loop refuses new
  positions on this station until you acknowledge the alert.** Data feed RED
  is informational (means a fetcher is broken; trades skip naturally because
  the model has no fresh distribution to score with).

**Acknowledging an alert.** On the Status tab, click "Ack" next to the alert
banner. This sets `acknowledged_at` on the latest health row and unblocks
the trade loop. Use only after you've investigated and decided the cause is
benign or already fixed.
"""

STATUS_TAB = """
Each tile reflects the **most recent** health-check evaluation. The
health-check job runs every 30 minutes via launchd; thresholds live in
`jobs/health_check.py`.

**DATA tile** — staleness of each ingestion feed (NBM, HRRR, METAR, Kalshi).
Red means a fetcher hasn't written in 3× its normal cadence.
**Tip:** Data RED + Model AMBER usually means a fetcher is dead, not a model
problem. Check the launchd logs for the offending job before tweaking the model.

**MODEL tile** — Brier score and |expected − realized edge| over the last 7 days
of settled fills. Red Brier means the probabilities are mis-calibrated; red
edge-diff means the model thinks it has more edge than reality is paying out.
**Historic incident:** 2026-04-30 had edge-diff at −$196.08 — that's well past
the $8/fill RED threshold. Had this dashboard existed, it would have flipped
RED on 04-29 evening and stopped the next day's losses.

**MARKETS tile** — count of open Kalshi markets per trade station.
Few markets = bot has nothing to score. Usually a Kalshi outage or weekend.

**RISK tile** — open notional as a fraction of bankroll. Caps stop a runaway
position-stacking scenario.

**P&L tile** — 7-day net P&L. Negative is fine in paper mode; very negative
breaks the surface and forces investigation.
"""

CALIBRATION_TAB = """
**This tab exists to catch the next 04-30.**

**Edge-gap line** — rolling daily |expected − realized| / n_settled. Each dot
is one day. The threshold band shows the AMBER/RED levels from health_check.
A single dot poking above the band is noise; a string of dots trending up is
the calibration drifting away.

**Brier-by-station** — lower is better. A perfect coin-flip forecast has
Brier 0.25; our post-fix model is around 0.14. Above 0.20 is suspicious.
A new station starts thin (small n) — give it 7 days before reading much
into the value.

**Reliability diagram** — bins forecast probability vs realized frequency.
On a calibrated model, the dots sit on the diagonal. A bowing-down curve
means the model is overconfident at high probabilities; bowing-up means
underconfident. The post-fix KNYC curve should be near-diagonal in the
0.30-0.70 range.

**Bias drift events** — rows from `bias_drift_event`. The drift detector
flags any (station, var, month, lead_day) cell that moved >1.5σ overnight
relative to its previous stddev. ALERT (>3σ) is the canary for "did our
fetcher break again?" — that's exactly what 04-30's corrupted NBM data
would have surfaced as before any fills happened.
"""

TRADING_TAB = """
**Open positions** — every unsettled paper fill. Mark-to-market uses the
latest Kalshi yes_ask snapshot; "to settle" is days until valid_date.

**Today's signals** — every signal evaluated by the trade loop today
(every 15 minutes). Filter by action:
- `OPEN` — the bot placed a paper fill.
- `SKIP/NO_EDGE` — fair vs market gap was too small after fees.
- `SKIP/DIVERGENCE` — fair diverged from market by >50pp; bot refused.
- `SKIP/BIAS_GATE` — the (station, var, month, lead_day) bias cell isn't
  fresh or well-sampled enough to trade. **This is the safety rail for
  fetch-only stations**: KORD/KMIA will show this skip reason until their
  bias tables fill out.
- `SKIP/TRIPWIRE_RED` — health_check has the station flagged RED. Trade
  loop refuses new positions until you Ack the alert on the Status tab.
- `SKIP/FEE_LOAD` — Kalshi fees would exceed 20% of contract price.

**Distribution preview** — for each trade-eligible station, the current
day's TMAX or TMIN distribution rendered as a CDF, with the HRRR point
overlaid and the Kalshi market buckets shaded. **You should be able to
eyeball "do we agree with the market" in one look.**
"""

DEEP_DIVE_TAB = """
**Counterfactual replay** — pick a date range and a parameter override
(widen factor, HRRR weight, divergence threshold), then re-score every
historical fill in the range under that hypothesis. Compare new fair_prob,
new Brier, new realized P&L against the actual ones. **This is the same
investigation tool we used to validate the 1.10x widen change** — making
it permanent so future tuning is data-driven instead of vibes-driven.

**NBM cycle inspector** — for any (station, valid_date), show every
NBM cycle's percentile distribution overlaid as CDFs. Adjacent cycles
disagreeing by more than ~3°F is the visible signature of a data
ingestion bug (the 04-30 incident showed 18°F swings).

**Per-fill ledger** — every settled fill with: divergence at fill time,
fair vs market, won/lost, net P&L. Sortable. Exportable.
"""

METRIC_TOOLTIPS = {
    "brier":       "Brier score: mean squared error between forecast probability "
                   "and actual outcome. 0 = perfect, 0.25 = coin flip, "
                   "≥0.20 = miscalibrated.",
    "edge_gap":    "Sum of (expected edge − realized P&L) across settled fills. "
                   "Negative = model overestimated its own edge. The 04-30 "
                   "incident hit −$196.08 on this metric.",
    "edge_per":    "Edge-gap divided by number of settled fills. Per-fill "
                   "normalisation lets us threshold on $/fill.",
    "open_n":      "Open paper fills (unsettled). Settles automatically after "
                   "valid_date passes and METAR is ingested.",
    "fair_prob":   "Bot's estimate of P(YES). Built from NBM percentile CDF + "
                   "shrunk station bias + HRRR same-day blend + intraday "
                   "floor/ceiling conditioning.",
    "divergence":  "|fair − market_mid|. The trade loop refuses to open at "
                   "divergence > 0.50.",
    "kelly":       "Quarter-Kelly fraction of bankroll. Capped at "
                   "MAX_POSITION_PCT (default 2%).",
    "lag_min":     "Minutes since latest row in this feed. Cadence-aware: "
                   "NBM 6h, HRRR 1h, METAR 30m, Kalshi 15m.",
    "sample_size": "Pairs of (forecast, observation) used to compute this "
                   "bias cell. n<10 is flagged by the bias gate.",
    "shrink":      "Empirical-Bayes shrink factor: n / (n + 10). New stations "
                   "ramp from 0 → 1 as samples accumulate.",
    "delta_sigma": "How many standard deviations the bias cell moved overnight. "
                   "Normalised by previous day's stddev. >3σ = drift ALERT.",
}
