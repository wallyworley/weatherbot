"""EXP-2026-011 — Market Reaction Latency report. RESEARCH-ONLY.

Implements docs/research/EXP_2026_011_MARKET_REACTION_LATENCY_AUDIT.md (LOCKED).

For each pre-registered public-info channel, measures the ordering
    event official_ts  ->  WeatherBot first_seen_at  ->  Kalshi reprice onset
using GENUINE forward `first_seen_at` from `info_provenance` (no historical backfill).
The single per-channel statistic is the distribution of
    lag = (first reprice onset after official_ts) - first_seen_at
reported as median lag and positive-lag fraction, per station, counting unique
station/date event-days. Measurement only; this script never trades and promotes nothing.

Run on the VPS against the VPS local DB. Do not export rows to local.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ---- LOCKED parameters (prereg sections 6, 7) ----
MATERIAL_MOVE_F = 0.10          # |market-center move| that counts as a reprice
PRE_MIN = 30                    # minutes before official_ts to establish the baseline center
POST_MIN = 60                   # minutes after official_ts to look for the onset
CAND_MEDIAN_LAG_MIN = 2.0       # candidate: median lag >= 2 min
CAND_POS_FRAC = 0.60            # candidate: positive-lag fraction >= 60%
CAND_MIN_EVENT_DAYS = 100       # candidate: >= 100 unique station/date event-days
CAND_MIN_STATIONS = 2           # candidate: >= 2 stations

# source_types + max plausible forward ingest latency (minutes) per channel. The latency cap is
# the operational definition of a GENUINE forward sighting: at instrumentation start the first
# poll of each source backfills recent items all stamped first_seen_at=now (e.g. METAR pulls 36h
# of history), which are NOT genuine first-sightings. Events whose (first_seen_at - official_ts)
# exceeds the cap are excluded as startup/stale backfill. Caps reflect real forward cadence:
# METAR obs arrive every few min and we poll often; model runs are available ~1.5-3.5h after
# their nominal cycle (official_ts = run cycle time, not availability).
CHANNEL_SOURCES = {
    "metar": (("metar", "metar_lowlat"), 60),
    "model_run": (("nbm_run", "hrrr_run", "gfs_run", "ecmwf_run"), 300),
    "cli": (("cli", "dsm"), 180),
}


# ---------------------------------------------------------------------------
# Pure lag core (unit-tested in tests/test_market_reaction_latency.py)
# ---------------------------------------------------------------------------
def reprice_onset(
    center_series: list[tuple[datetime, float]],
    official_ts: datetime,
    pre_min: int = PRE_MIN,
    post_min: int = POST_MIN,
    threshold: float = MATERIAL_MOVE_F,
) -> datetime | None:
    """First timestamp in (official_ts, official_ts+post_min] whose center deviates from the
    baseline by >= threshold. Baseline = last center at or before official_ts within pre_min.
    `center_series` must be sorted ascending by timestamp. Returns None if no baseline or no
    onset (interval-censored)."""
    baseline = None
    lo = official_ts - timedelta(minutes=pre_min)
    for ts, c in center_series:
        if lo <= ts <= official_ts:
            baseline = c
    if baseline is None:
        return None
    hi = official_ts + timedelta(minutes=post_min)
    for ts, c in center_series:
        if official_ts < ts <= hi and abs(c - baseline) >= threshold:
            return ts
    return None


def _lag_minutes(onset: datetime, first_seen_at: datetime) -> float:
    return (onset - first_seen_at).total_seconds() / 60.0


# ---------------------------------------------------------------------------
# DB access (VPS local DB only)
# ---------------------------------------------------------------------------
def _station_local_date(ts: datetime, tz_name: str) -> date:
    from zoneinfo import ZoneInfo

    return ts.astimezone(ZoneInfo(tz_name)).date()


def _bucket_mid(lower_f, upper_f, typical_width: float) -> float | None:
    if lower_f is not None and upper_f is not None:
        return (float(lower_f) + float(upper_f)) / 2.0
    if lower_f is None and upper_f is not None:
        return float(upper_f) - typical_width / 2.0
    if lower_f is not None and upper_f is None:
        return float(lower_f) + typical_width / 2.0
    return None


def _center_series(cur, station: str, valid_date: date) -> list[tuple[datetime, float]]:
    """Kalshi market-center time series for one station/date (lead-0 TMAX market)."""
    cur.execute(
        """
        SELECT ms.ts AS ts, ms.ticker AS ticker,
               ((ms.yes_ask::float + ms.yes_bid::float) / 2.0) AS p,
               km.lower_f AS lower_f, km.upper_f AS upper_f
          FROM market_snapshot ms
          JOIN kalshi_market km ON km.ticker = ms.ticker
         WHERE km.station = %(station)s AND km.valid_date = %(vd)s AND km.var = 'TMAX_DAILY'
           AND ms.yes_ask IS NOT NULL AND ms.yes_bid IS NOT NULL
         ORDER BY ms.ts
        """,
        {"station": station, "vd": valid_date},
    )
    rows = cur.fetchall()
    widths = [
        float(r["upper_f"]) - float(r["lower_f"])
        for r in rows
        if r["lower_f"] is not None and r["upper_f"] is not None and r["upper_f"] > r["lower_f"]
    ]
    typical = statistics.median(widths) if widths else 2.0
    by_ts: dict[datetime, list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        mid = _bucket_mid(r["lower_f"], r["upper_f"], typical)
        p = r["p"]
        if mid is None or p is None:
            continue
        by_ts[r["ts"]].append((float(p), mid))
    series: list[tuple[datetime, float]] = []
    for ts in sorted(by_ts):
        legs = by_ts[ts]
        tot = sum(p for p, _ in legs)
        if tot <= 0:
            continue
        center = sum(p * mid for p, mid in legs) / tot
        series.append((ts, center))
    return series


def _provenance_events(cur, source_types: tuple[str, ...], since: datetime) -> list[dict]:
    cur.execute(
        """
        SELECT source_type, station, official_ts, first_seen_at
          FROM info_provenance
         WHERE source_type = ANY(%(src)s)
           AND first_seen_at >= %(since)s
           AND station IS NOT NULL
           AND official_ts IS NOT NULL
         ORDER BY first_seen_at
        """,
        {"src": list(source_types), "since": since},
    )
    return list(cur.fetchall())


# ---------------------------------------------------------------------------
# scoring + aggregation
# ---------------------------------------------------------------------------
@dataclass
class LagRow:
    station: str
    valid_date: date
    lag_min: float


def _station_tz(cur, station: str) -> str:
    cur.execute("SELECT tz FROM stations WHERE code = %(s)s", {"s": station})
    r = cur.fetchone()
    return (r and r["tz"]) or "America/New_York"


def score_channel(
    cur, source_types: tuple[str, ...], since: datetime, max_latency_min: int
) -> tuple[list[LagRow], dict]:
    events = _provenance_events(cur, source_types, since)
    series_cache: dict[tuple[str, date], list[tuple[datetime, float]]] = {}
    tz_cache: dict[str, str] = {}
    out: list[LagRow] = []
    diag = defaultdict(int)
    diag["events"] = len(events)
    for ev in events:
        st = ev["station"]
        ot = ev["official_ts"]
        fs = ev["first_seen_at"]
        # forward-genuineness guard: drop startup/stale backfill (see CHANNEL_SOURCES note)
        if (fs - ot).total_seconds() / 60.0 > max_latency_min:
            diag["stale_backfill_excluded"] += 1
            continue
        tz = tz_cache.get(st) or tz_cache.setdefault(st, _station_tz(cur, st))
        vd = _station_local_date(ot, tz)
        key = (st, vd)
        if key not in series_cache:
            series_cache[key] = _center_series(cur, st, vd)
        series = series_cache[key]
        if not series:
            diag["no_market_series"] += 1
            continue
        onset = reprice_onset(series, ot)
        if onset is None:
            diag["no_onset_censored"] += 1
            continue
        out.append(LagRow(st, vd, _lag_minutes(onset, fs)))
    return out, dict(diag)


def _summarize(rows: list[LagRow]) -> dict:
    if not rows:
        return {"n": 0, "event_days": 0, "stations": 0}
    lags = [r.lag_min for r in rows]
    return {
        "n": len(rows),
        "event_days": len({(r.station, r.valid_date) for r in rows}),
        "stations": len({r.station for r in rows}),
        "median_lag": statistics.median(lags),
        "pos_frac": sum(1 for x in lags if x > 0) / len(lags),
    }


def _fmt(s: dict) -> str:
    if s["n"] == 0:
        return "n=0 (no scored events)"
    return (
        f"events={s['n']} event-days={s['event_days']} stations={s['stations']} "
        f"median_lag={s['median_lag']:+.2f}min pos_lag_frac={s['pos_frac']:.0%}"
    )


def _candidate(s: dict) -> bool:
    return (
        s["n"] > 0
        and s["median_lag"] >= CAND_MEDIAN_LAG_MIN
        and s["pos_frac"] >= CAND_POS_FRAC
        and s["event_days"] >= CAND_MIN_EVENT_DAYS
        and s["stations"] >= CAND_MIN_STATIONS
    )


def run(since: datetime, out_path: Path) -> None:
    from weather_bot.data import persistence

    lines = [
        "# EXP-2026-011 — Market Reaction Latency Results",
        "",
        f"_generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC_",
        "",
        "Implements `EXP_2026_011_MARKET_REACTION_LATENCY_AUDIT.md` (LOCKED). Measurement only;"
        " no trading change. Forward-collected `first_seen_at` only (since "
        f"{since:%Y-%m-%d %H:%M} UTC).",
        "",
        "lag = (first Kalshi reprice onset after official_ts) minus first_seen_at. "
        "POSITIVE lag = market repriced AFTER WeatherBot first saw the event (potential edge). "
        f"Reprice = |center move| >= {MATERIAL_MOVE_F:g} F. Polling is interval-censored: onset"
        " timing is an upper bound bounded by snapshot cadence.",
        "",
        f"Candidate gate (per channel): median lag >= {CAND_MEDIAN_LAG_MIN:g} min AND positive-"
        f"lag fraction >= {CAND_POS_FRAC:.0%} AND >= {CAND_MIN_EVENT_DAYS} event-days AND >= "
        f"{CAND_MIN_STATIONS} stations.",
        "",
    ]
    any_candidate = False
    with persistence.connect() as conn, conn.cursor() as cur:
        for channel, (sources, max_latency_min) in CHANNEL_SOURCES.items():
            rows, diag = score_channel(cur, sources, since, max_latency_min)
            overall = _summarize(rows)
            is_cand = _candidate(overall)
            any_candidate = any_candidate or is_cand
            lines.append(
                f"## Channel: {channel}  (sources: {', '.join(sources)}; "
                f"forward-latency cap {max_latency_min} min)"
            )
            lines.append("")
            lines.append(f"- overall: {_fmt(overall)}")
            lines.append(f"- diagnostics: {diag}")
            if overall["n"]:
                per_station = defaultdict(list)
                for r in rows:
                    per_station[r.station].append(r)
                for st in sorted(per_station):
                    lines.append(f"  - {st}: {_fmt(_summarize(per_station[st]))}")
            verdict = "CANDIDATE" if is_cand else (
                "insufficient sample" if overall["event_days"] < CAND_MIN_EVENT_DAYS else "no candidate"
            )
            lines.append(f"- **channel verdict: {verdict}**")
            lines.append("")

    lines.append("## Cross-venue (Polymarket lead) — channel 4")
    lines.append("")
    lines.append(
        "Not scored in this build: requires rules/source-verified same-station comparability "
        "between Kalshi and Polymarket (do not infer from city names) plus paired Kalshi/"
        "Polymarket center series. Deferred until the comparability map is locked. Provenance "
        "(`polymarket_book`) is collecting."
    )
    lines.append("")
    lines.append("## DSM channel")
    lines.append("")
    lines.append(
        "DSM is not first-class instrumented (no durable live table). Reported as "
        "not-yet-forward-instrumented per the handoff."
    )
    lines.append("")
    lines.append("## Audit status")
    lines.append("")
    if any_candidate:
        lines.append(
            "At least one channel meets the candidate gate. Per the locked decision rule this "
            "opens a SEPARATE paper-only signal pre-registration (EXP-2026-012). No trading "
            "change here; the candidate still requires the full promotion bar."
        )
    else:
        lines.append(
            "No channel meets the candidate gate yet. If event-days are below the threshold this "
            "is a forward-collection-in-progress run, not a closure. The latency axis is only "
            "closed once the committed forward window is reached with no candidate."
        )
    lines.append("")
    out_path.write_text("\n".join(lines))
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description="EXP-2026-011 market reaction latency (research-only).")
    ap.add_argument(
        "--since",
        required=True,
        help="ISO UTC cutoff; only first_seen_at >= this is used (forward-collected only).",
    )
    ap.add_argument("--out", default="research/reports/exp_2026_011_results.md")
    args = ap.parse_args()
    since = datetime.fromisoformat(args.since)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    run(since, Path(args.out))


if __name__ == "__main__":
    main()
