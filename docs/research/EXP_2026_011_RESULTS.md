# EXP-2026-011 — Market Reaction Latency: Status (forward collection in progress)

**Date:** 2026-06-09
**Status:** Instrumentation live, report tool built and validated. **NOT a verdict.** Forward
collection in progress. Measurement only; no production trading change.

This is a status record, not the audit result. The audit conclusion is deferred until the
committed forward window is reached (see §4). The canonical machine-generated run lives on the
VPS at `research/reports/exp_2026_011_results.md` and is regenerated on each run.

## 1. What is live (verified on the VPS, 2026-06-09 ~14:40 UTC)

- `info_provenance` table created (migration applied), additive fail-safe `first_seen_at`
  writes deployed on the live ingest path.
- Genuine forward collection confirmed writing: `metar` (10,022 rows), `kalshi_book` (1,764),
  `polymarket_book` (44), all stamped from 14:29 UTC onward. `nbm_run` / model-run and `cli`
  appear on their slower cadence (6h / daily).
- Report tool `research/market_reaction_latency.py` built, unit-tested (pure onset core, 5
  cases), deployed, and validated end to end on the VPS.

## 2. First smoke run (one partial day, NOT evidence)

Per channel, forward-only, with the genuineness cap applied:

| channel | events | event-days | stations | median lag | positive-lag frac | verdict |
|---|---:|---:|---:|---:|---:|---|
| metar | 133 | 20 | 20 | -23.3 min | 11% | insufficient sample |
| model_run | 34 | 17 | 17 | -147.6 min | 0% | insufficient sample (see §3) |
| cli | 0 | 0 | 0 | - | - | insufficient sample |

Direction note (NOT a conclusion): the METAR channel leans negative (the market tends to move
before we ingest the observation), consistent with the prior in the prereg §3. One partial day
is far below the 100 event-day gate; this is a pipeline validation, not a finding.

## 3. Methodological findings from the smoke (to resolve before the evidence run)

1. **Startup-backfill artifact (fixed).** The first poll after instrumentation backfilled ~36h
   of METAR all stamped `first_seen_at=now`, producing spurious -1,200 min lags. Added a
   per-channel max-ingest-latency cap (METAR 60m, model-run 300m, CLI 180m) as the operational
   definition of a genuine forward sighting; 9,806 stale rows excluded on the smoke. This is a
   refinement to the prereg's "genuine forward first_seen" and should be confirmed by codex.
2. **Model-run `official_ts` semantics (open).** `official_ts` for a model run is the nominal
   cycle time (e.g. 12Z), but the run only becomes available ~1.5 to 3.5h later, after which
   the market reprices continuously before we poll. So lag measured against the cycle time is
   structurally negative and not meaningful. The model-run channel needs the run's actual
   availability timestamp (or our first poll treated as the availability proxy with a different
   estimand) before it can be scored honestly. Flagged for codex.
3. **Cross-venue (Polymarket) not scored.** Requires a rules/source-verified same-station
   comparability map between Kalshi and Polymarket (do not infer from city names). Provenance
   is collecting; scoring deferred until the map is locked.
   **UPDATE 2026-06-09 (late):** two developments, see
   `EXP_2026_011_CROSS_VENUE_MAP_VERIFICATION.md`. (a) **Collection defect found and
   fixed**: the fetcher was polling the two hardcoded, resolved May-16 events (KLGA/KORD,
   both non-comparable) — the channel had zero usable forward data; slugs are now generated
   daily for the verified-comparable set. (b) **A2 map expanded 2 → 7 comparable stations**
   (added KAUS, KSEA, KLAX, KHOU, KSFO; KDFW newly excluded — PM settles Love Field KDAL):
   the 100-event-day gate moves from ~50 days out to **~15 days** (~2026-06-25). Genuine
   forward collection on the expanded set starts 2026-06-10.
4. **DSM not first-class instrumented.** Reported as not-yet-forward-instrumented per the
   handoff. Add provenance only if a durable DSM capture path is created.
5. **Polling is interval-censored.** Kalshi/Polymarket book timing is bounded by snapshot
   cadence; onset is an upper bound. The candidate rule already requires the margin to survive
   adverse polling uncertainty.

## 4. Forward-collection clock and the evidence run

- The candidate gate (prereg §7) needs >= 100 unique station/date event-days and >= 2 stations
  per channel, on genuine forward data.
- METAR accrues ~20 event-days per calendar day (20 stations), so the METAR sample threshold is
  reached in roughly one week. A comfortable, robust evidence run (sample margin, several
  model-run cycles, and time to resolve §3 items 2 and 3) is targeted for **on or after
  2026-06-23**.
- Until then this stays a forward-collection-in-progress status. Below-threshold event-days is
  **in progress, not a closure**. The latency axis is closed only when the committed window is
  reached with no candidate on any channel.

## 4b. High-cadence Kalshi WebSocket collector LIVE (amendment A5, 2026-06-09)

Research-only `orderbook_delta` collector is deployed and running as a systemd service
(`weatherbot-kalshi-ws.service`, active + enabled). Subscribe-only (no order path), never read
by production. After a schema fix (Kalshi uses the dollar-fp format `yes_dollars_fp` /
`price_dollars` / `delta_fp`, not integer-cent arrays), verified healthy: 17,515 deltas +
1,710 snapshots across 342 tickers in 46 min, all with derived top-of-book and exchange
timestamps. Volume control: persist only when a ticker's top-of-book changes (~8M rows over the
collection window vs ~370M raw). This tightens reprice-onset timing against the polling
censoring caveat before the evidence run. Table: `kalshi_ws_book_event`.

## 5. Hard constraints honored

Broad collection stays on for all stations; KHOU stays collected and is not trade-enabled; no
production probability, sizing, execution, gate, or trading logic changed; evidence work runs
on the VPS against the VPS DB and rows are not exported to local. A positive finding only opens
a separate EXP-2026-012 paper-only pre-registration; it never trades here.
