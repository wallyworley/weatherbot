"""Wind-direction-vs-forecast-error audit.

Tests the claim from the Reddit Kalshi weather article that wind direction
predicts when the morning forecast will be systematically wrong:
  - LA NE wind (Santa Ana)  → forecast underprediction (actual much hotter)
  - LA W wind (marine layer) → forecast overprediction (actual cooler)
  - Chicago SW wind          → forecast underprediction
  - NYC ahead of warm front  → forecast underprediction

For each (station, valid_date) we compute:
  - residual = cli_tmax_f - morning_nbm_p50
  - dominant heating-hours wind direction (10am-3pm local) parsed from METAR raw
  - bucket the wind direction into 8 compass octants

Then group by (station, wind_octant) and report mean residual, n, std.
A non-zero mean residual for a wind bucket = the forecast has a systematic bias
on those days that the bot could potentially exploit.

This is RESEARCH ONLY — does not touch the trading code.

Usage:
    python -m weather_bot.research.wind_direction_audit
    python -m weather_bot.research.wind_direction_audit --days 90
    python -m weather_bot.research.wind_direction_audit --station KLAX --days 60
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from statistics import mean, stdev

from weather_bot.data import persistence


# METAR wind groups look like "25013KT" or "VRB05KT" or "25013G22KT" (gusts).
# We want the direction (first 3 digits). VRB = variable, skip.
_WIND_RE = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(?:G\d{2,3})?KT\b")


def parse_wind_dir(raw: str) -> int | None:
    """Return wind direction in degrees (0-360), or None if VRB/missing."""
    m = _WIND_RE.search(raw or "")
    if not m:
        return None
    if m.group(1) == "VRB":
        return None
    d = int(m.group(1))
    return d if 0 <= d <= 360 else None


def degrees_to_octant(deg: int) -> str:
    """Convert 0-360° to 8-point compass (N, NE, E, SE, S, SW, W, NW)."""
    # N spans 337.5-22.5, NE 22.5-67.5, etc.
    octants = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((deg + 22.5) // 45) % 8
    return octants[idx]


def circular_mean(degrees: list[int]) -> int | None:
    """Mean wind direction handling 360°/0° wrap. Returns None if empty."""
    import math
    if not degrees:
        return None
    sin_sum = sum(math.sin(math.radians(d)) for d in degrees)
    cos_sum = sum(math.cos(math.radians(d)) for d in degrees)
    if sin_sum == 0 and cos_sum == 0:
        return None
    deg = math.degrees(math.atan2(sin_sum, cos_sum))
    return int(deg % 360)


def fetch_station_days(station: str, days: int) -> list[dict]:
    """Pull (valid_date, cli_tmax, nbm_p50, [wind_dirs]) per day for a station."""
    # Morning NBM run = earliest run_time of valid_date where run_time is
    # before noon local. We take the latest such run.
    sql = """
        WITH nbm_morning AS (
            SELECT DISTINCT ON (station, valid_date)
                   station, valid_date, value AS nbm_p50, run_time
              FROM prob_forecast
             WHERE model = 'NBM_QMD'
               AND var = 'TMAX_DAILY'
               AND percentile = 50
               AND station = %s
               AND valid_date >= CURRENT_DATE - (%s || ' days')::interval
               AND (run_time AT TIME ZONE (
                       SELECT tz FROM stations WHERE code = %s
                   ))::time <= '10:00'::time
             ORDER BY station, valid_date, run_time DESC
        ),
        wind_samples AS (
            SELECT m.station,
                   (m.obs_time AT TIME ZONE st.tz)::date AS local_date,
                   m.raw
              FROM metar_obs m
              JOIN stations st ON st.code = m.station
             WHERE m.station = %s
               AND (m.obs_time AT TIME ZONE st.tz)::date
                   >= CURRENT_DATE - (%s || ' days')::interval
               AND (m.obs_time AT TIME ZONE st.tz)::time
                   BETWEEN '10:00'::time AND '15:00'::time
        )
        SELECT n.valid_date, n.nbm_p50, c.tmax_f AS cli_tmax,
               ARRAY_AGG(w.raw) FILTER (WHERE w.raw IS NOT NULL) AS metar_raws
          FROM nbm_morning n
          LEFT JOIN cli_obs c
                 ON c.station = n.station AND c.local_date = n.valid_date
          LEFT JOIN wind_samples w
                 ON w.station = n.station AND w.local_date = n.valid_date
         WHERE c.tmax_f IS NOT NULL
         GROUP BY n.valid_date, n.nbm_p50, c.tmax_f
         ORDER BY n.valid_date DESC
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, days, station, station, days))
        rows = cur.fetchall()

    out = []
    for r in rows:
        raws = r.get("metar_raws") or []
        dirs = [d for d in (parse_wind_dir(raw) for raw in raws) if d is not None]
        if not dirs:
            continue
        out.append(dict(
            valid_date=r["valid_date"],
            cli_tmax=float(r["cli_tmax"]),
            nbm_p50=float(r["nbm_p50"]),
            residual=float(r["cli_tmax"]) - float(r["nbm_p50"]),
            wind_dir=circular_mean(dirs),
            wind_n=len(dirs),
        ))
    return out


def fetch_active_stations() -> list[str]:
    sql = """
      SELECT DISTINCT station FROM cli_obs
       WHERE local_date >= CURRENT_DATE - INTERVAL '90 days'
         AND tmax_f IS NOT NULL
       ORDER BY station
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [r["station"] for r in cur.fetchall()]


def analyze_station(station: str, days: int) -> None:
    rows = fetch_station_days(station, days)
    if not rows:
        return

    by_octant: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r["wind_dir"] is None:
            continue
        oct_ = degrees_to_octant(r["wind_dir"])
        by_octant[oct_].append(r["residual"])

    overall_mean = mean(r["residual"] for r in rows)
    overall_std = stdev(r["residual"] for r in rows) if len(rows) > 1 else 0.0

    print(f"\n=== {station} · n={len(rows)} days · overall residual {overall_mean:+.2f}°F (σ={overall_std:.2f}) ===")
    print(f"{'Wind':<6} {'n':<4} {'mean':<8} {'std':<8} {'vs overall':<12}")
    for oct_ in ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]:
        vals = by_octant.get(oct_, [])
        if not vals:
            continue
        m = mean(vals)
        s = stdev(vals) if len(vals) > 1 else 0.0
        delta = m - overall_mean
        flag = ""
        # Flag buckets where mean shifts ≥1°F vs overall with n≥5
        if len(vals) >= 5 and abs(delta) >= 1.0:
            flag = " ← " + ("WARM" if delta > 0 else "COOL") + " bias"
        print(f"{oct_:<6} {len(vals):<4} {m:+.2f}°F  {s:.2f}    {delta:+.2f}°F{flag}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--station", type=str, default=None,
                    help="Single station code (e.g. KLAX). Default: all with data.")
    args = ap.parse_args()

    stations = [args.station] if args.station else fetch_active_stations()
    for st in stations:
        analyze_station(st, args.days)


if __name__ == "__main__":
    main()
