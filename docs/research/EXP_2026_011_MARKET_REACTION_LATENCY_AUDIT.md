# EXP-2026-011 — Market Reaction Latency Audit (Pre-Registration)

**Date:** 2026-06-09
**Status:** LOCKED 2026-06-09 after codex review.
**Type:** Measurement program (audit). NOT a trading project.
**Constraint:** research-only, paper-only, no production probability / sizing / execution
change. This audit does not trade and does not promote anything. Promotion still requires
`WEATHERBOT_PROMOTION_CRITERIA.md` via a separate, later pre-registration.

> Locked fields: channels (§4), provenance keying (§5), lag statistic (§6),
> material-move threshold (§6), candidate rule (§7), and validity controls (§8). They may
> not be added, removed, or re-specified. All results reported including null findings.
> Changes require a new prereg.

---

## 1. Why this exists (and why it does not contradict the closed edge question)

The edge investigation (audit, B1 to B3, C1, C1b, C2) closed the **forecast-accuracy** axis:
WeatherBot's forecast does not beat the market. The C2 closure was explicitly framed as a
program decision "given current data and models, not a proof of impossibility," and named the
trigger to reopen: genuinely new data or a new pre-registered source.

This audit tests a **different axis**: reaction latency. The hypothesis is not "our forecast is
better." It is "the market is briefly stale after a public-information event that we can also
observe." Source: the PR&R field-notes thesis that the weather-market edge is speed, not
accuracy ("reading ECMWF and NOAA output before the crowd does, and acting on the gap before
it closes").

We have never measured the latency ordering cleanly. EXP-2026-009 measured the METAR channel's
market reaction but used official observation timestamps, not the time WeatherBot first saw the
information, and it never touched the model-run or cross-venue channels. This audit fills that
gap.

## 2. The single question

For each pre-registered public-information event type, what is the ordering of:

```
event official_ts  ->  WeatherBot first_seen_at  ->  Kalshi market reprice onset
```

If the market reprice onset reliably PRECEDES or coincides with `first_seen_at`, there is no
capturable latency edge on that channel. If the reprice onset reliably LAGS `first_seen_at` by
a material margin, that channel is a candidate for a later, separately pre-registered paper-only
signal test.

## 3. Null hypothesis and honest prior

**Null:** the market reprices before or simultaneously with `first_seen_at` on every channel.
This is the expected outcome. Reasons: the PR&R notes themselves say the liquid-market window
compressed from 30 to 60 minutes (2024) to 5 to 15 minutes now and keeps compressing; US
daily-temp Kalshi markets are reasonably arbitraged; EXP-2026-009 already leans negative on the
METAR channel (`market_moved_before_or_at_recent_metar` 4,227 slightly outnumbers
`market_moves_after_recent_metar` 4,059, and the C2 nowcast on the "after" cohort still lost);
and we are a single-VPS hobby-scale system unlikely to out-speed professional market makers on
model-run reads. The value of the audit is converting these priors into a direct measurement,
not an expectation of finding edge.

## 4. The pre-registered channels (LOCKED at review)

Exactly four event types. No others are added post hoc.

1. **METAR / HFMETAR high update.** A fresh observation raises `metar_max_so_far`, especially
   when it approaches or crosses a bucket boundary. (Lowest prior: largely measured, leans
   negative. Included as the calibration baseline.)
2. **Model-run availability.** A new NBM / HRRR / GFS / ECMWF run becomes available to our
   ingest and shifts bucket fair value past a threshold. (Untested.)
3. **CLI / DSM intraday update.** A new or revised NWS text product (gap fills, corrections)
   lands before the book moves. (Untested.)
4. **Cross-venue Polymarket lead.** The Polymarket bucket distribution for the same station and
   date disagrees with Kalshi, and Kalshi later moves toward Polymarket. (Untested; highest
   upside, because it does not require us to out-speed sharps on model reads, only that two
   venues lag each other.)

## 5. Provenance instrumentation: `first_seen_at` (the missing piece)

The audit is impossible without recording when our system first observed each item. We store
official timestamps today; we do not store ingest time. Proposed research-only table:

```
info_provenance
  id              bigserial primary key
  source_type     text not null
                           -- metar | metar_lowlat | cli | dsm |
                           --   nbm_run | hrrr_run | gfs_run | ecmwf_run |
                           --   kalshi_book | polymarket_book
  station         text     -- ICAO; null only for non-station-keyed items
  official_ts     timestamptz  -- obs_time / run_time / issue_time / exchange book ts
  event_key       text not null
                           -- stable natural event key, unique with source_type
  first_seen_at   timestamptz not null default now()  -- when OUR ingest wrote it
  value_summary   jsonb    -- e.g. {"max_f": 87.0} | {"run_avail": true} | {"center_f": 86.2}
  ingest_host     text     -- single clock source (the VPS); for skew auditing
  created_at      timestamptz not null default now()
  updated_at      timestamptz not null default now()
```

Properties:
- **Additive only.** A write into a new table from the existing ingest path. No probability,
  sizing, gating, or execution logic changes. (Touches the live ingest path, so this piece
  needs explicit operator approval before wiring, per the standing live-system rule.)
- **Dedupe rule.** `UNIQUE (source_type, event_key)`. Repeated fetches update
  `value_summary` / `updated_at` but preserve the earliest `first_seen_at` with
  `LEAST(existing.first_seen_at, excluded.first_seen_at)`. This prevents polling retries
  from manufacturing new events.
- For `kalshi_book` and `polymarket_book`, `first_seen_at` is our snapshot fetch time and
  `official_ts` is the exchange-reported book timestamp where available, otherwise the fetch
  time. Because current Kalshi/Polymarket capture is polling, book-event timing is interval
  censored until a stream/webhook collector exists.
- **Forward collection is mandatory for the untested channels.** Genuine `first_seen_at` cannot
  be reconstructed from history. The METAR channel may use EXP-2026-009 as a lower-bound
  approximation, but the model-run, CLI/DSM, and cross-venue findings are only valid on data
  collected AFTER instrumentation goes live. The audit therefore commits to a forward window
  before any conclusion (see §7).

## 6. The measurement (LOCKED)

Reuses the EXP-2026-009 backbone, which already records
`market_center_move_next_{1,5,10,30,60}m` and the timing labels. Adds the `first_seen_at` keying
and the model-run / cross-venue channels.

Per event, compute:
- market center / bucket probabilities at the last snapshot before the event,
- market move at +1, +5, +10, +30, +60 minutes after the event,
- **reprice onset**: the first post-event interval whose absolute market-center move exceeds a
  pre-committed material threshold: **0.10 F on market center**, matching
  `MATERIAL_CENTER_MOVE_F` in EXP-2026-009,
- **lag** = (reprice onset timestamp) minus `first_seen_at`.

The single pre-registered statistic per channel is the **distribution of lag**, reported as
median and the fraction of events with lag strictly positive (market moved after we saw it),
broken out per station and per liquidity tier. One statistic per channel. No search over
alternative event or move definitions.

For the model-run channel, the event is source-native and not selected by WeatherBot's
current losing probability estimate: a run event is included when the new run's daily center
for an open station/date bucket set differs from the previous same-source run by **>= 0.5 F**
OR when its fixed NBM-shape bucket vector differs by **>= 5 percentage points** in any bucket.
Those thresholds are locked for event inclusion and are not trading thresholds.

## 7. Decision rule (pre-committed) — this audit only

This audit decides ONLY whether a candidate channel exists. It never trades.

- **Candidate found** on a channel if, on forward-collected data with genuine `first_seen_at`:
  **median lag >= 2 minutes** AND **positive-lag fraction >= 60%** AND it holds across
  **>= 2 stations** (or a station-specific rationale is locked first) AND at least
  **100 event-days** for the channel (unique station/date pairs with one or more qualifying
  events; raw repeated snapshots do not count toward this minimum) AND it survives the
  clock-skew checks in §8. Result: open a SEPARATE pre-registration (EXP-2026-012) for a
  paper-only signal test with the full strict bar (market-relative Brier AND RPS improvement,
  station-date cluster-robust CI excluding zero, OOS on fresh station-days, no leakage, no
  production change).
- **No candidate** on any channel: the latency axis is closed alongside the accuracy axis.
  Observation-only stands, now on both axes. No production change.

In neither branch does EXP-2026-011 change production logic or place any trade.

## 8. Validity controls (a latency study lives or dies here)

- **Clock discipline.** All timestamps UTC from a single clock source (the VPS), NTP-synced.
  A 30-second clock skew destroys a 1 to 10 minute measurement. Record `ingest_host`; audit
  drift before trusting any lag. Exchange-reported book timestamps are cross-checked against
  our fetch time to bound skew.
- **Polling interval caveat.** Current book capture is polling. Any result from polling data
  must report interval-censored onset bounds. A channel can be marked "candidate found" only
  if the positive-lag margin remains after applying the polling interval as adverse timing
  uncertainty, or after a research-only streaming/high-cadence collector confirms it.
- **`first_seen_at` is genuine, never backfilled.** Untested-channel conclusions use forward
  data only (§5).
- **No look-ahead in any eventual signal.** Here the future market move is a measured label,
  not a feature. A passing channel only earns a separate signal prereg; it is not itself a
  signal.
- **Cluster-robustness.** Any eventual CI clusters by station-date, not per-snapshot (the
  EXP-C2 reviewer note); snapshot-level independence is anti-conservative.
- **Settlement** is used only for later scoring, never in the latency ordering.

## 9. Anti-slice-mining guardrails

Four channels and one lag statistic per channel, locked at review. No post-hoc channel
addition, no searching over move thresholds or event definitions, no per-station cherry-picking
without a rationale locked in advance. Forward window and event-count minimums committed before
looking at outcomes. All channels reported including nulls.

## 10. Relationship to the existing program

- Backbone: EXP-2026-009 (`research/market_information_forensics.py`) stays running, all
  stations, KHOU included, reports on the VPS DB. No production change.
- This prereg adds: the `info_provenance` table, model-run and cross-venue event capture, and
  the latency-ordering report. It is the measurement layer codex proposed (provenance ->
  reaction detector -> latency stats -> candidate gate).
- Personal-project routing: all artifacts live in the repo (`docs/research/` + `STATE.md`),
  not the team vault.

## 11. What gets built after lock (for reference, not yet authorized)

1. `info_provenance` table + additive ingest-path writes (operator approval required).
2. Forward collection for the committed window.
3. `research/market_reaction_latency.py`: the per-channel lag report, run on the VPS.
4. Results to `EXP_2026_011_RESULTS.md` + registry EXP-2026-011, then the §7 decision.

---

**Codex review resolved 2026-06-09:** schema now has stable event keys and earliest-first-seen
dedupe; material move is locked to 0.10 F; candidate thresholds are numeric; raw repeated
events are replaced by event-days for sample size; model-run inclusion is source-native rather
than selected by WeatherBot's current probabilities; polling interval censoring is an explicit
validity control.

---

## LOCKED AMENDMENTS (2026-06-09, post codex resolutions)

Source: `EXP_2026_011_CODEX_OPEN_ITEMS.md` + `EXP_2026_011_CODEX_RESOLUTIONS.md`. These amend the
locked sections above and are themselves locked.

**A1 — Model-run estimand = Option A (amends §6).** For the model-run channel, `official_ts`
(= `run_time`) is metadata used only for cycle/source grouping. The latency anchor is
`first_seen_at`. Score by taking the baseline center at the start of the pre-window
(`first_seen_at - pre_window`) and finding the first material move (0.10 F) across the window
`(first_seen_at - pre_window, first_seen_at + post_window]`. lag = onset minus `first_seen_at`,
so a move BEFORE `first_seen_at` (the market already priced the run) is a negative lag, and a
move after is positive. Nominal cycle-time lag is never counted as evidence. Option B
(`value_summary.available_ts`) is an optional later diagnostic only, not required, not a new
column. METAR and CLI keep `official_ts` (obs/issue time) as the anchor.

**A2 — Cross-venue same-station map (amends §4 ch.4, §5).** Score the Polymarket channel only on
a rules/source-verified same-station map (seed from `research/polymarket_crosscheck.py`):
comparable = **KATL, KMIA**; excluded as non-comparable = **KNYC** (Kalshi KNYC vs Polymarket
KLGA), **KMDW** (KMDW vs KORD), **KDEN** (Denver Intl vs Buckley SFB); all other stations
EXCLUDED until rules/source verified. A pair is eligible only if the Polymarket resolution source
is persisted/cited and maps to the same physical station as the Kalshi market. Bucket alignment:
each venue center on its own normalized ladder midpoints; require same var, same date, F units,
substantial support overlap; for any bucket-level or EXP-2026-012 scoring, re-bin Polymarket to
the Kalshi ladder. Same-station gaps may be source basis (Polymarket raw Wunderground vs Kalshi
NWS/CLI), not edge.

**A3 — Forward genuineness = cutoff AND cap (amends §5, §8).** Use BOTH a hard
instrumentation-start cutoff (`first_seen_at >= since`) AND a per-channel max-ingest-latency cap
on `first_seen_at - official_ts`: **METAR 60 min, model-run 480 min, CLI 360 min**. The
start cutoff is the primary guard against day-one startup backfill; the cap is the second guard
against stale rows. (Supersedes the provisional 300 min model-run cap and 180 min CLI cap.)

**A4 — DSM (amends §4 ch.3).** Report DSM as not-forward-instrumented for this audit. The
CLI/DSM channel reports CLI forward evidence and states DSM coverage unavailable. DSM becomes
prospective only from the date a durable DSM capture path is added by explicit amendment.

**A5 — Research-only Kalshi WebSocket collector (amends §8 polling caveat).** Before the
evidence run, stand up a research-only Kalshi WebSocket market-data collector (NOT a trading
path, NOT a webhook) to tighten onset timing against polling censoring. Endpoint
`wss://external-api-ws.kalshi.com/trade-api/ws/v2`, API-key authenticated, subscribe
`orderbook_delta` (snapshot then deltas, with exchange `ts`/`ts_ms`) for active weather tickers
only. Store raw messages + receipt timestamps in a research-only table; never read it from
production probabilities, sizing, execution, gates, or station activation.

**A6 — Backtest scope (clarifies §3, §8).** Historical `market_snapshot` replay is exploratory
only (hypothesis generation, code-path validation). The audit VERDICT remains forward-only on
genuine `first_seen_at`; pre-instrumentation history has no trustworthy first-seen and cannot
produce a latency verdict.
