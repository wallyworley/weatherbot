# EXP-2026-011 Claude Handoff — Forward Collection + Latency Report

**Date:** 2026-06-09
**Status:** ready for Claude after codex steps 1-3. VPS migration/test/smoke completed.

## What Codex Completed

- Locked `EXP_2026_011_MARKET_REACTION_LATENCY_AUDIT.md`.
- Added `info_provenance` schema to `db/schema.sql`.
- Added migration `db/migrations/2026-06-09_info_provenance.sql`.
- Added additive provenance writes for:
  - live METAR/HFMETAR rows (`source_type='metar'`)
  - live NBM / HRRR / GFS / ECMWF model-run rows
  - scheduled CLI pulls (`source_type='cli'`, default only for normal <=2-day pulls)
  - Kalshi book snapshots (`source_type='kalshi_book'`)
  - Polymarket book snapshots (`source_type='polymarket_book'`)
- Added `tests/test_info_provenance.py`.
- Updated the personal vault note:
  `/Users/walterworley/Documents/claude-obsidian/wiki/questions/WeatherBot Market Information Forensics.md`.

## Hard Constraints

- Run evidence-producing work on the VPS itself against the VPS local PostgreSQL DB.
- Do not SSH/tunnel DB data back to local for analysis.
- Continue broad collection for all configured stations.
- Keep KHOU/Houston collection enabled.
- Do not trade-enable KHOU or any new station.
- Do not change production probability, sizing, execution, gates, or trading logic.
- EXP-2026-011 is measurement only. A positive finding only opens EXP-2026-012.

## First VPS Actions

Already completed by codex on 2026-06-09:

```bash
psql "$DATABASE_URL" -f db/migrations/2026-06-09_info_provenance.sql
.venv/bin/python -m pytest tests/test_info_provenance.py
```

Codex also ran a minimal live-path smoke on the VPS:

```bash
.venv/bin/python - <<'PY'
from weather_bot.data import metar_fetcher, persistence
rows = metar_fetcher.fetch("KNYC", hours=1)
rows = metar_fetcher.filter_implausible_swings(rows)
if rows:
    persistence.upsert_metar(rows, record_provenance=True)
print(f"knyc_recent_metar_rows={len(rows)}")
PY
```

Observed smoke result: `knyc_recent_metar_rows=1`, and `info_provenance` contained one
`source_type='metar'` row afterward.

Optional next smoke for Claude, using the local DB:

```bash
.venv/bin/python -m weather_bot.jobs.pull_metar
.venv/bin/python -m weather_bot.jobs.pull_nbm
.venv/bin/python -m weather_bot.jobs.pull_gfs
.venv/bin/python -m weather_bot.jobs.pull_ecmwf
.venv/bin/python -m weather_bot.jobs.pull_hrrr
.venv/bin/python -m weather_bot.jobs.pull_cli --days-back 2
.venv/bin/python -m weather_bot.jobs.pull_kalshi_markets
.venv/bin/python -m weather_bot.jobs.pull_polymarket
```

Local verification on the VPS only:

```sql
SELECT source_type, count(*), min(first_seen_at), max(first_seen_at)
FROM info_provenance
GROUP BY source_type
ORDER BY source_type;
```

Do not export the rows back to local. Summarize counts in the result note.

## Remaining Build

1. Forward collect enough genuine `first_seen_at` rows. Do not use historical backfilled
   provenance as evidence.
2. Build `research/market_reaction_latency.py`.
3. The report should read from `info_provenance`, `market_snapshot`,
   `external_market_snapshot`, `kalshi_market`, and the EXP-2026-009 backbone.
4. Output `docs/research/EXP_2026_011_RESULTS.md` and update
   `WEATHERBOT_EXPERIMENT_REGISTRY.md`.

## Locked Audit Rules

- Channels: METAR/HFMETAR, model-run availability, CLI/DSM, cross-venue Polymarket lead.
- Material market-center move: `0.10 F`.
- Lag: first reprice onset timestamp minus `first_seen_at`.
- Candidate threshold: median lag `>= 2 min`, positive-lag fraction `>= 60%`, `>= 100`
  event-days, `>= 2` stations, and clock/polling uncertainty checks passed.
- Raw repeated snapshots do not satisfy sample size; count unique station/date event-days.
- Polling is interval-censored. A candidate only survives if the positive-lag margin remains
  after adverse polling-interval uncertainty, or a research-only high-cadence/streaming
  collector confirms it.

## Known Gaps For Claude

- DSM is not currently persisted in a first-class live table. Existing forensics reads DSM from
  a longitudinal research file. Either add DSM provenance when a durable DSM capture path is
  created, or report DSM as not yet forward-instrumented.
- Kalshi and Polymarket book provenance is polling-based. Treat onset timing as bounded, not
  exact.
- Cross-venue station comparability must be rules/source verified. Do not infer same-station
  comparability from city names.
