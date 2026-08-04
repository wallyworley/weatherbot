# Weather Prediction Context Brief - KNYC 2026-05-16

_generated 2026-05-17 00:41 UTC_

Purpose: provide qualitative context for a human or AI reviewer. This brief is not an execution signal.

## Settlement / Observation

- CLI TMAX: None
- daily_obs TMAX: None (None)
- settled/preferred TMAX: None

## Latest Forecasts

- NBM percentiles: p5=78.2, p10=79.1, p25=80.3, p50=81.4, p75=82.5, p90=83.2, p95=83.4
- HRRR 2026-05-16 23:00:00+00:00: tmax=75.0
- GFS 2026-05-17 00:00:00+00:00: tmax=74.7
- ECMWF 2026-05-16 18:00:00+00:00: tmax=80.5
- GFS_ENS 2026-05-17 00:00:00+00:00: members=31 mean=74.4 sigma=0.5
- ECMWF_IFS_ENS 2026-05-17 00:00:00+00:00: members=51 mean=74.8 sigma=1.7
- ECMWF_AIFS_ENS 2026-05-17 00:00:00+00:00: members=51 mean=72.4 sigma=1.9
- WEATHERNEXT2 2026-05-16 12:00:00+00:00: members=64 mean=74.5 sigma=2.4

## Kalshi Buckets / Latest Signals

| ticker | bucket | status | fair | mid | action | skip |
|---|---|---|---:|---:|---|---|
| KXHIGHNY-26MAY16-T76 | None to 76.0 | active | 0.050 | - | SKIP | TRIPWIRE_RED |
| KXHIGHNY-26MAY16-B76.5 | 76.0 to 78.0 | active | 0.050 | - | SKIP | TRIPWIRE_RED |
| KXHIGHNY-26MAY16-B78.5 | 78.0 to 80.0 | active | 0.787 | - | SKIP | TRIPWIRE_RED |
| KXHIGHNY-26MAY16-B80.5 | 80.0 to 82.0 | active | 0.121 | - | SKIP | TRIPWIRE_RED |
| KXHIGHNY-26MAY16-B82.5 | 82.0 to 84.0 | active | 0.076 | - | SKIP | TRIPWIRE_RED |
| KXHIGHNY-26MAY16-T83 | 84.0 to None | active | 0.070 | - | SKIP | TRIPWIRE_RED |

## AI Review Guardrails

- Look for context the numeric model may miss: settlement wording, station mismatch, stale markets, forecast run jumps, obs-vs-forecast contradictions, and boundary risk.
- Do not recommend an order unless the deterministic bot already shows positive fee-aware EV.
- Output should be advisory only: `context_supports`, `context_warns`, or `insufficient_context`.
