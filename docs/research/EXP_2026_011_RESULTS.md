# EXP-2026-011 Market Reaction Latency: RESULTS (final scoring)

**Date:** 2026-08-04
**Status:** **CLOSED. No candidate on any channel.** Channels 1-3 scored negative (1 and 2 at
full pre-registered power); channel 4 terminated unscored at 14% of the required sample.
**Verdict scope:** measurement only. No production trading change was made by this audit, and
none follows from it.

> Supersedes the 2026-06-09 forward-collection status record (preserved in git history).
> Locked procedure: `EXP_2026_011_MARKET_REACTION_LATENCY_AUDIT.md` (prereg, amendments A1-A7).
> Sole surviving evidence run: `EXP_2026_011_EVIDENCE_RUN_2026-06-23.md` (in-repo copy of the
> machine-generated report; the VPS original and its source data no longer exist).

---

## 1. What was scored, and on what data

The locked §7 gate was applied to the machine-generated run of 2026-06-23 21:02 UTC, covering
forward-collected genuine `first_seen_at` from **2026-06-09 14:29 -> 2026-06-23 21:02 UTC**
(~14 days).

**The intended fuller evidence run could not be produced.** Collection continued to
**2026-07-02 23:03 UTC** (~9 further days), but that window was never scored: the project was
retired, the collectors were stopped, and the `weather_bot` database was subsequently dropped.
**No surviving backup contains the audit's data.** Verified against all three that exist: the
2026-05-09 full dump (predates `info_provenance` entirely), and the 2026-07-02 results dump plus
schema dump held on Google Drive (`weatherbot-vps-backup-20260702`), neither of which includes
`info_provenance`, `kalshi_ws_book_event`, or `polymarket_ws_book_event`. The Jul 2 backup
preserved the trading and results tables (`paper_fill`, `paper_order`, `signal`, `verification`,
`kalshi_settled_market`), not the latency provenance layer.

The Jun 9 → Jun 23 run is therefore the complete and final evidence base.

§2.1 below shows why the lost window does not change the verdict.

## 2. Channel scoring against the locked §7 gate

Gate, all conditions required: **median lag ≥ +2 min** AND **positive-lag fraction ≥ 60%** AND
**≥ 2 stations** AND **≥ 100 event-days** AND survives the §8 validity controls.

Positive lag = the market repriced AFTER WeatherBot first saw the event (the potential edge).

| # | Channel | Event-days | Stations | Median lag | Pos-lag | Verdict |
|---|---|---:|---:|---:|---:|---|
| 1 | METAR | 299 ✅ | 20 ✅ | −10.56 min ❌ | 28% ❌ | **No candidate**, at full power |
| 2 | Model-run | 299 ✅ | 20 ✅ | −16.25 min ❌ | 27% ❌ | **No candidate**, at full power |
| 3 | CLI | 86 ❌ | 14 ✅ | −280.24 min ❌ | 0% ❌ | **No candidate**, gate unreachable |
| 4 | Cross-venue | 14 ❌ | 5 ✅ | −18.22 min ❌ | 29% ❌ | **Not scoreable** |

**Channel 1 (METAR)** is the pre-registered calibration baseline, and it behaved as the §3 prior
expected. Negative at 19 of 20 stations. The market has already moved on the observation about
10 minutes before our ingest records it.

**Channel 2 (model-run)**: the decisive result. This was the untested channel carrying the
PR&R thesis that the weather-market edge is speed on model reads. It is negative at full power:
the market reprices a new run a median of **16 minutes before** we first see it. No station
passes; the best is KLAS at −8.17 min / 38% positive. A single-VPS hobby-scale ingest does not
out-speed the market to model output, as the honest prior stated.

**Channel 3 (CLI)**: formally 14 event-days short of the sample minimum, but the short sample
is **not** the binding failure. The channel returned **0 positive lags out of 86 events across
all 14 stations**. Even if every one of the missing 14 event-days were 100% positive, the
channel reaches 14% against a 60% bar. CLI is the settlement text, published the following
morning; the market has fully priced the day long before it lands.

**Channel 4 (cross-venue Polymarket)**: **not scoreable.** 14 scored episodes against a 100
event-day requirement (14%), with 121 episodes left-censored. By the prereg's own assessment
this was the highest-upside channel, and it never reached power. It is now unresolvable: the
`polymarket_ws_book_event` and `info_provenance` data were destroyed with the database.

**DSM**: reported as not forward-instrumented, exactly as pre-registered in amendment A4.

### 2.1 The lost 2026-06-23 → 2026-07-02 window does not change the verdict

At the observed accrual rates, the ~9 unscored days would have produced:

- **Channel 3 (CLI):** ~6.1 event-days/day → roughly 140 event-days, **clearing** the sample
  gate. But the positive-lag fraction was 0/86, so the channel still fails on direction. The
  lost window would have tidied the bookkeeping and left the verdict unchanged.
- **Channel 4 (cross-venue):** ~1 scored episode/day → roughly 23 event-days, still far short
  of 100. The lost window would **not** have rescued this channel.

No other channel was sample-limited. The destroyed data therefore affects the formal
completeness of channel 3's sample and nothing else.

## 3. Validity controls (§8)

- **Polling interval censoring works in favor of the conclusion.** A move is detected at the
  poll *after* it truly occurred, so measured onset ≥ true onset, and therefore measured lag ≥
  true lag. Every channel median is already negative, so the true lags are at least as negative.
  Censoring can manufacture false positives, not false negatives, and no positives were found.
  The negative findings are conservative under the censoring caveat, which is the direction the
  prereg required before any "candidate found" call.
- **Genuineness (A3).** The instrumentation-start cutoff and per-channel max-ingest-latency caps
  were applied. Excluded stale rows: 16,785 (METAR), 352 (model-run), 198 (CLI).
- **Clock discipline.** Single clock source (the VPS), Kalshi series taken from the A5 WebSocket
  stream with skew-checked exchange timestamps.
- **Forward-only.** No pre-instrumentation history was used, per A6.

## 4. The one positive slice, and why it is locked out

**KNYC** is the single station showing positive lag: +18.91 min, 97% positive, 232 events over
14 event-days. It does **not** open a candidate, for two independent reasons:

1. **Procedurally excluded.** The gate requires ≥ 2 stations, or a station-specific rationale
   **locked in advance**. None was locked, and §9 explicitly forbids per-station cherry-picking
   without one. Promoting KNYC here would be exactly the slice-mining the prereg was written to
   prevent.
2. **Very likely a measurement artifact.** KNYC logged 232 events against 2,000-2,600 at every
   other station, because Central Park is a non-ASOS coop site with no HFMETAR feed. With
   roughly 10x sparser events, "the first material move after we looked" mechanically lands
   later in the window. This mechanism can no longer be verified now that the source data is
   destroyed, so it is recorded as the probable artifact rather than an established finding.

## 5. Decision (§7)

Per the locked decision rule, no candidate on any channel closes the latency axis.

**The latency axis is CLOSED.** The honest characterization of the closure:

- Channels 1 and 2 are **decisively negative at full pre-registered power**. These carried the
  core hypothesis.
- Channel 3 is **negative on direction**, with a gate that is arithmetically unreachable
  regardless of sample.
- Channel 4 is **terminated unscored**, not scored negative. It never reached power and can no
  longer do so.

This is not the clean four-channel null the prereg envisioned, and the record should not claim
otherwise. Two points nonetheless support the closure rather than leaving channel 4 genuinely
open:

- Channel 4's own descriptive evidence leaned null: 126 of 146 directional episodes were
  Polymarket-colder, consistent with the documented persistent source basis (Polymarket raw
  Wunderground vs Kalshi NWS/CLI) rather than a real lead, and 11 of 25 non-censored episodes
  showed no Kalshi follow at all.
- A channel-4 candidate would only have opened **EXP-2026-012**, a separate paper-only
  pre-registration at the strict market-relative Brier-and-RPS bar. Every prior test at that bar
  (EXP-C1, C1b, C2/EXP-2026-010, EXP-2026-013) failed.

**No production change.** The bot was already paper-only and is now retired. Nothing here
reopens the accuracy axis or argues against retirement.

## 6. Reopening conditions

The latency axis would only be worth reopening on genuinely new instrumentation, not new
analysis of the same design:

- Channel 4 is the only channel with an unanswered question, and answering it requires fresh
  forward collection (~100 event-days of scoreable cross-venue episodes) on a verified
  same-station map, with the PM-side source basis separated from any genuine lead.
- Channels 1-3 are answered. Re-running them on new data would be re-testing a settled result.

Any such work requires a fresh pre-registration; this one is spent.
