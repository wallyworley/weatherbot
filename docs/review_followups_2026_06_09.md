# 6/9 re-evaluation — consolidated follow-ups

Items to check on or after 2026-06-09, gathered across the 2026-06-03 session.
Most are env-tunable; none touch real money (paper mode).

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

## C. Lessons from the Polymarket trader (jobs/polymarket_trader_watch.py)

The studied trader is +$150K realized on weather. **Decoded strategy: buy ~1¢ tail
brackets, SELL the intraday rally** (100% of his profitable positions resolved NO
at settlement yet booked gains). His favorite-buying (0.70+) LOSES.

**The key reframe for us:** the cheap-tail YES strategy is a WINNER *iff you have a
reliable intraday exit*. Our cheap-tail YES lost (−$189, held 0/95) because our
take-profit fires only ~15% of the time — we buy the tail but fail to sell the
rally. `YES_TAIL_GATE` blocks the *entry* (treats the symptom); his edge says the
real lever is the **exit**.

- Re-examine the **take-profit mechanism** (threshold 0.70, slippage haircut,
  book-size gate): is it too slow/conservative to catch 1¢→20-50¢ tail spikes?
- Consider an A/B: relax `YES_TAIL_GATE` on a sleeve WITH an aggressive
  sell-the-rally exit, vs. keep blocking. Needs the exit fixed first.
- CAVEAT: transferability depends on Kalshi intraday liquidity to exit into;
  Polymarket may be deeper. Verify before sizing.
- `STRATEGY_NOTES.md` (on VPS, regenerated every 3h) is the living study.

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

## E. New monitors now running (VPS timers)

- `weatherbot-reddit.timer` (30 min) — Prilo WeatherEdge + PredictionsMarkets
  (weather-only) → email digest. Review by email.
- `weatherbot-pmtrader.timer` (3 h) — Polymarket trader trades → email digest +
  regenerates STRATEGY_NOTES.md. Goal: learn, not copy.
- Both email via Resend (`RESEND_API_KEY` in VPS .env). Inbox/scratch gitignored.
