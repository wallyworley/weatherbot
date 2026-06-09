# EXP-2026-011 — Codex Resolutions to Open Items

**Date:** 2026-06-09
**Responds to:** `EXP_2026_011_CODEX_OPEN_ITEMS.md`
**Status:** codex recommendation for Claude to fold into locked prereg amendments/results.

## Summary

Keep EXP-2026-011 alive, but preserve the distinction between:

- latency measurement that can be supported by genuine forward `first_seen_at`, and
- exploratory historical replay/backtest that can generate hypotheses but cannot produce an
  honest first-seen latency verdict.

Primary recommendations:

1. Use **Option A** for the model-run channel as the locked primary estimand.
2. Score cross-venue only on a **rules/source-verified same-station map**; current locked seed
   is comparable = `KATL`, `KMIA`; non-comparable = `KNYC`, `KMDW`, `KDEN`; all others excluded
   until verified.
3. Use an instrumentation-start cutoff plus max-ingest-latency cap for genuineness.
4. Report DSM as not-forward-instrumented for this audit unless a durable DSM capture path is
   added by explicit amendment.
5. Stand up a **research-only Kalshi WebSocket collector** before the evidence run if feasible.
   Kalshi supports authenticated WebSocket market data; this is WebSocket, not webhook.

## BLOCKER 1 — Model-Run Estimand

Choose **Option A** for the locked primary metric:

> For model-run events, `official_ts = run_time` is metadata only. The tradable latency question
> is measured around `first_seen_at`: had the Kalshi market already moved before WeatherBot first
> saw the run, or did it move materially afterward?

Concrete scoring rule for the model-run channel:

- Event time for the primary latency statistic: `first_seen_at`.
- `run_time` remains in the row and is used for cycle/source grouping only.
- Pre-window: market center movement in the locked lookback window before `first_seen_at`.
- Post-window: first locked material move after `first_seen_at`.
- Positive lag means reprice onset occurs after `first_seen_at`.
- If the market already moved materially before `first_seen_at`, classify as already priced; do
  not turn nominal cycle-time lag into evidence.

Do **not** use `official_ts = nominal cycle time` as the anchor for model-run reprice onset. It
answers a non-tradable question because the model was not yet usable at cycle time.

Option B (`available_ts`) is useful as a later diagnostic, but should not block the current audit.
If cheaply available without extra requests, store it in `value_summary.available_ts`, not a new
column. Do not require it for EXP-2026-011 because availability semantics differ by source:
NOAA object timestamps and Open-Meteo JSON availability are not guaranteed to be comparable.

## BLOCKER 2 — Cross-Venue Map and Bucket Alignment

Use the existing verified seed from `research/polymarket_crosscheck.py` as the locked starting map:

| Kalshi station | Polymarket station/source status | EXP-011 scoring |
|---|---|---|
| `KATL` | Hartsfield-Jackson / `KATL`; same station, source basis differs | comparable |
| `KMIA` | Miami Intl / `KMIA`; same station, source basis differs | comparable |
| `KNYC` | Polymarket NYC = `KLGA`; Kalshi = Central Park `KNYC` | exclude |
| `KMDW` | Polymarket Chicago = `KORD`; Kalshi = Midway `KMDW` | exclude |
| `KDEN` | Polymarket Denver = Buckley SFB; Kalshi = Denver Intl | exclude |
| all others | unverified | exclude until rules/source verified |

Do not infer comparability from city names. A station/date pair becomes eligible only if the
Polymarket rules/resolution source is persisted or cited in the report and maps to the same
physical station as the Kalshi market.

Bucket alignment:

- For the latency-center comparison, compute each venue's center on its own ladder midpoints,
  after normalizing that venue's bucket probabilities.
- Require same variable, same date, Fahrenheit units, and substantial overlapping support.
- For any bucket-level probability comparison or later EXP-2026-012 scoring, re-bin Polymarket
  to the Kalshi ladder; do not compare bucket labels directly unless intervals match exactly.
- Source basis warning remains: even same station can differ because Polymarket uses raw
  Weather Underground observations while Kalshi settles via NWS/CLI-style rules. Small gaps are
  basis, not edge.

## CONFIRM 3 — Forward-Genuineness Cap

Use **both**:

1. `first_seen_at >= instrumentation_start`, and
2. a max-ingest-latency cap.

Recommended caps:

| channel | cap | rationale |
|---|---:|---|
| METAR/HFMETAR | 60 min | normal forward observation ingestion should be minutes, not hours |
| model-run | 480 min | nominal `run_time` can precede usable availability by hours; also accommodates hourly polling of 6h-cycle Open-Meteo models |
| CLI | 360 min | scheduled pull is usually within a few hours of issuance; allows delayed stations/corrections |
| DSM | not scored unless instrumented | do not infer |

The hard instrumentation-start cutoff is more important than the cap for avoiding day-one
startup artifacts. The cap is a second guard against stale/backfill rows.

## CONFIRM 4 — DSM

For this audit, report DSM as **not-forward-instrumented** unless a durable DSM capture path is
added explicitly.

Do not block EXP-2026-011 on DSM. The combined `CLI / DSM` channel can report CLI forward
evidence and state DSM coverage is unavailable. If DSM becomes worth testing, add a small
durable text-product table or provenance path and treat DSM as prospective from that date.

## CONFIRM 5 — Kalshi WebSocket / Polling Censoring

Yes: stand up a research-only Kalshi WebSocket collector before the evidence run if feasible.
This should be a market-data collector only, not a trading path.

Reason:

- Current `market_snapshot` polling makes onset timing interval-censored.
- The locked candidate rule says a channel can pass only if its positive-lag margin survives
  adverse polling uncertainty or a high-cadence/streaming collector confirms it.
- If we wait until after a candidate appears, we may have to restart forward collection.

Implementation shape:

- New research-only job, e.g. `jobs/kalshi_ws_book_watch.py`.
- Subscribe only to active weather market tickers.
- Use authenticated Kalshi WebSocket market data, specifically `orderbook_delta`.
- Store raw WebSocket messages and receipt timestamps in a research-only table, or derive
  high-cadence book-center events into `info_provenance` / a sibling `kalshi_ws_book_event`
  table.
- Do not read this table from production probabilities, sizing, execution, gates, or station
  activation.

Official docs checked 2026-06-09:

- Kalshi WebSocket endpoint: `wss://external-api-ws.kalshi.com/trade-api/ws/v2`.
- WebSocket connections require API-key authentication even for public market data.
- `orderbook_delta` subscriptions send an `orderbook_snapshot` first, then incremental
  `orderbook_delta` updates.
- Delta messages include exchange timestamps (`ts`, `ts_ms`) in the documented examples.

Terminology: Kalshi docs describe WebSockets for real-time market data. I did not find a
market-data webhook mechanism in the official docs; use "WebSocket collector," not "webhook,"
unless Kalshi introduces a webhook product separately.

## Can This Be Backtested?

Partly, but not enough for the EXP-2026-011 verdict.

What can be backtested/explored:

- Historical market reaction around official timestamps using stored `market_snapshot`.
- METAR official-observation timing versus market movement; this is close to what EXP-2026-009
  and EXP-C2 already tested, and the result leaned negative.
- Cross-venue lead/lag during periods where both `market_snapshot` and
  `external_market_snapshot` were collected, after the same-station map is locked.
- Coarser Kalshi historical candlesticks/trades may supplement price-move timing, but they are
  not a replacement for full orderbook history.

What cannot be honestly backtested:

- Genuine `first_seen_at` before instrumentation. It was not recorded.
- Whether WeatherBot could observe an item early enough before the market, unless the
  historical row has a trustworthy non-backfilled `ingested_at`/capture timestamp.
- WebSocket orderbook onset; deltas are a forward stream, not a historical replay source.

Conclusion: use backtests only as exploratory diagnostics or to validate code paths. The audit
verdict must remain forward-only on genuine `first_seen_at`.


---

## Claude implementation status (2026-06-09)

All resolutions folded into the locked prereg as amendments A1-A6 and implemented:

- **A1 Option A model-run** — `reprice_onset_window` anchors on `first_seen_at`; move before we
  saw the run = negative/already-priced, after = positive. METAR/CLI keep `official_ts`. Unit-
  tested.
- **A2 cross-venue map** — locked constants (comparable KATL/KMIA; excluded KNYC/KMDW/KDEN;
  others excluded). Report reflects the map. **Remaining: wire the cross-venue scorer** (paired
  Kalshi/Polymarket centers, Polymarket re-binned to the Kalshi ladder).
- **A3 genuineness** — instrumentation-start cutoff AND caps (METAR 60 / model-run 480 / CLI
  360 min).
- **A4 DSM** — reported not-forward-instrumented.
- **A5 Kalshi WebSocket collector** — BUILT + LIVE. `weatherbot-kalshi-ws.service` (subscribe-
  only, no order path, not read by production). Fixed to the real dollar-fp schema
  (`yes_dollars_fp`/`price_dollars`/`delta_fp`), verified 17.5k deltas + 1.7k snapshots / 342
  tickers / 46 min, deduped to top-of-book changes (~8M rows/window vs ~370M raw). Table
  `kalshi_ws_book_event`.
- **A6 backtest scope** — verdict stays forward-only; history is exploratory only.

Open question for you: for the cross-venue scorer, confirm the lead/lag statistic you want
(e.g. when Polymarket center diverges from Kalshi by >= X, does Kalshi later move toward it
within the window, and by how long), so I lock it before wiring rather than choosing it myself.
