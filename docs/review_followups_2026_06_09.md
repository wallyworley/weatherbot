# 6/9 re-evaluation — consolidated follow-ups

Items gathered 2026-06-03; **frame corrected 2026-06-05.** Mostly env-tunable;
none touch real money (paper mode).

> **CORRECTION 2026-06-05 — read this first.** An earlier version of the frame
> below claimed the Polymarket trader's edge is "buy cheap, **sell the intraday
> rally**." **That is false.** His public activity (verified on the VPS store:
> `research/pm_trader/`) is **1,303 trades, all BUY, 0 SELL**; he exits via
> REDEEM/MERGE and **holds to resolution**. He is a *selection* player, not an
> executor. The authoritative write-up is
> [`docs/bot_performance_evaluation_2026_06_04.md`](bot_performance_evaluation_2026_06_04.md);
> where this doc and that one disagree, that one wins. The frame is rewritten
> accordingly below.

## The frame for this whole re-eval (corrected)

Both external sources we brought in **win on the same thing: selection** — being
right about *which brackets are underpriced* — held to resolution. They are not
two different halves; they are two confirmations of one point.

- **Prilo WeatherEdge = forecaster.** Edge = the temperature *distribution*: regime
  conditioning (Miami humidity runs hot, Chicago lake-breeze suppresses, SFO marine
  cap), correct σ, and the discipline to sit out the many no-edge days.
- **The Polymarket trader = selection + structure.** Verified buy-only barbell
  (median entry ~5¢, but ~43% of buys are favorites too), **held to resolution**,
  winners redeemed at $1, across a hugely diversified book of city-days. Every
  weather position is `negativeRisk=true`; profits are realized through
  Polymarket's redeem/merge accounting (exact per-position mechanics **not fully
  reconstructable** — do not over-interpret). He does **not** trade in and out.

**So the lesson is singular and blunt: the edge is in SELECTION, and our bot fails
exactly there** — it bets where it disagrees with the market (winner's curse) and
is wrong there (0 of 43 held bets won since 06-01; see eval doc §2). Neither trader
hands us a mechanical trick to copy. And the structures differ: Polymarket negRisk
brackets ≠ Kalshi temperature buckets, so we cannot port the trader's book.

**On the exit / "sell the rally" idea:** that was *our own* observation from *our*
snapshots (16 of 43 held losers rallied before decaying), **not** something either
trader does. It is a modest damage-limiter (~23% of losers), not an edge. Treat it
as a band-aid while we fix selection — not as the main event.

**The test to anchor every 6/9 decision:** *Does this change improve our SELECTION
(being right about which bracket), or is it just a band-aid (exit tuning, stop-loss,
station pausing)?* Selection is the only thing that creates an edge; everything else
limits damage on a book that doesn't yet have one.

## A. Calibration / entry changes shipped 2026-05-29 (verify they worked)

Deployed: DIVERGENCE cap 0.50→0.20 (`MAX_FAIR_MKT_DIVERGENCE`), widen 1.40→1.10,
calibrator `PROB_CALIBRATION_PRIOR_N` 35→15 + `MAX_DELTA` 0.15→0.20, divergence-
bypass gate-evasion fix. See `docs/review_2026_05_29_opus48.md`.

1. Re-run the **exit-independent reliability curve** (CLI truth, all fills) on
   fills from 2026-05-30+. Want the flat ~+17pp overconfidence compressed to <5pp.
2. **Watch for under-confidence overshoot** (widen off + calibrator stronger). If
   actual > predicted, nudge `PROB_CALIBRATION_PRIOR_N` back up via `.env`.
3. Confirm the 0.20 divergence cap isn't starving volume; loosen toward 0.25 if so.

## B. Time-of-day finding (backtest, 2026-06-03)

Settled P&L by local entry hour (since 5/15, TMAX): **overnight 00-06 is the only
profitable bucket (+$57, 49.7% win); 09:00→afternoon entries bled −$771.** The
afternoon TMAX entries are toxic — likely intraday-floor renormalization
manufacturing edges vs an efficient (peak-mostly-in) market = the winner's-curse
cohort. This is the REVERSE of "wait for morning obs."

- Check whether the **divergence cap already fixed the afternoon bleed** on
  post-deploy fills (those manufactured edges are high-divergence → now blocked).
- If still bleeding after a week: candidate **"no-new-opens 09:00–15:00 local" gate**
  (data now justifies it).

## C. Lessons from the Polymarket trader (jobs/polymarket_trader_watch.py) — corrected

**Verified facts (VPS store `research/pm_trader/`, 2026-06-05):** 1,303 trades, all
BUY, 0 SELL; 526 positions (502 weather), all `negativeRisk=true`; ~93% of decided
weather positions positive by Polymarket-reported realizedPnl; net ≈ **+$146,695**.
Edge concentrated in cheap Yes: **under-10¢ = 425 positions, 365/365 decided
positive, +$152,218**; the 50¢+ buckets are collectively negative (90–100¢ ≈
−$5,715). He **holds to resolution** (exits via REDEEM/MERGE) — no exit trick.

**What this means for our bot:**
- It **strengthens** the finding that our problem is **selection**, not trade
  management. He is right about which cheap brackets are underpriced; we are not.
- It does **not** justify mechanically copying him into Kalshi — negativeRisk
  multi-bracket structure ≠ Kalshi temperature buckets. No direct port.
- The negRisk realizedPnl accounting is **not fully reconstructable** from public
  data; the study notes correctly warn against over-interpreting it.
- Action: use the study as a *selection* benchmark (which city-days / brackets does
  a proven selector load up on, vs. what we price), **not** as an execution recipe.
- `STRATEGY_NOTES.md` (regenerated every 3h) is the living study. Export for an
  outside reviewer: `STRATEGY_NOTES.md`, `positions_store.json`, `trades.jsonl`
  from `/opt/weather_bot/research/pm_trader/` (gitignored — not visible in the repo).

## D. Polymarket cross-check (research/polymarket_crosscheck.py)

Independent market reference. **VERIFIED, not guessed:** Polymarket settles on
Wunderground raw obs (we/Kalshi = NWS CLI — a real source basis), and per-city
settlement STATIONS differ. Only **KMIA + KATL** are verified same-station/
comparable; NYC=LaGuardia, Chicago=O'Hare, Denver=Buckley are NOT (we trade
Central Park / Midway / Denver Intl).

- First live signal: **our KATL model underweights the 78-79°F bracket** that BOTH
  Polymarket and Kalshi price at ~0.59 (we say 0.32). Per winner's-curse, the
  model is the likely outlier — investigate KATL distribution/bias.
- **Expand the verified map**: read the rules for KDFW, KAUS, KSEA, KPHX, KBOS,
  KLAX, KHOU and enable only confirmed same-station cities. Don't guess.
- **A cross-venue entry gate is NOT yet validated.** The trader store is his
  activity/positions, not a clean historical Polymarket-vs-Kalshi same-station
  panel. To justify a gate we need the cross-check tool's same-station snapshots
  *accumulated over time* on the (currently only 2) comparable cities, then a
  backtest. Until then this is a research signal, not a trading rule.

## E. New monitors now running (VPS timers)

- `weatherbot-reddit.timer` (30 min) — Prilo WeatherEdge + PredictionsMarkets
  (weather-only) → email digest. Review by email.
- `weatherbot-pmtrader.timer` (3 h) — Polymarket trader trades → email digest +
  regenerates STRATEGY_NOTES.md. Goal: learn, not copy.
- Both email via Resend (`RESEND_API_KEY` in VPS .env). Inbox/scratch gitignored.
