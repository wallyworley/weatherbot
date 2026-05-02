"""Multi-station spatial triangulation.

Pulls METAR for neighbor stations defined per-primary in
`config.NEIGHBOR_STATIONS`. Stores in the same `metar_obs` table as
primaries — separation is purely conceptual: neighbors don't drive
settlement, bias correction, or daily_obs (those iterate ACTIVE_STATIONS),
they exist to compute regional spread / gradient features for the dashboard
and the reversal-risk score.

Inspired by dailydewpoint.com's NYC observation panel which pulls JFK/LGA/
EWR/TEB/CDW/SMQ to triangulate the temperature field around Central Park.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from weather_bot.config import NEIGHBOR_STATIONS
from weather_bot.data import metar_fetcher, persistence

log = logging.getLogger(__name__)


def pull_all_neighbors(hours: int = 6) -> int:
    """Pull recent METARs for every neighbor station. Returns total rows persisted."""
    all_rows: list[dict] = []
    seen: set[str] = set()
    for primary, neighbors in NEIGHBOR_STATIONS.items():
        for n in neighbors:
            if n.code in seen:
                continue   # avoid double-pulling shared neighbors
            seen.add(n.code)
            try:
                rows = metar_fetcher.fetch(n.code, hours)
            except Exception as exc:
                log.warning("neighbor METAR %s failed: %s", n.code, exc)
                continue
            log.info("neighbor METAR %s (near %s): %d obs", n.code, primary, len(rows))
            all_rows.extend(rows)
    if all_rows:
        persistence.upsert_metar(all_rows)
    return len(all_rows)


def regional_field(primary_station: str, lookback_min: int = 90) -> Optional[dict]:
    """Compute the regional temperature field around a primary station.

    Returns a dict with:
      - primary_temp:    latest temp at primary station
      - neighbors:       list of {code, temp_f, vs_primary} for each neighbor
      - mean:            mean across primary + neighbors
      - spread:          max - min across all stations
      - vs_mean:         primary - mean (positive = primary running warm vs neighbors)
      - n_stations:      count of stations with recent obs

    Returns None if primary has no recent obs.
    """
    if primary_station not in NEIGHBOR_STATIONS:
        return None
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=lookback_min)
    codes = [primary_station] + [n.code for n in NEIGHBOR_STATIONS[primary_station]]

    sql = """
    SELECT DISTINCT ON (station) station, temp_f, obs_time
      FROM metar_obs
     WHERE station = ANY(%s) AND obs_time >= %s AND temp_f IS NOT NULL
     ORDER BY station, obs_time DESC
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (codes, cutoff))
        rows = {r["station"]: r for r in cur.fetchall()}

    if primary_station not in rows:
        return None
    primary_temp = float(rows[primary_station]["temp_f"])
    neighbor_records = []
    all_temps = [primary_temp]
    for nb in NEIGHBOR_STATIONS[primary_station]:
        if nb.code not in rows:
            continue
        t = float(rows[nb.code]["temp_f"])
        neighbor_records.append({
            "code": nb.code, "name": nb.name,
            "temp_f": t, "vs_primary": t - primary_temp,
            "obs_time": rows[nb.code]["obs_time"].isoformat(),
        })
        all_temps.append(t)

    mean = sum(all_temps) / len(all_temps)
    return {
        "primary_station": primary_station,
        "primary_temp": primary_temp,
        "primary_obs_time": rows[primary_station]["obs_time"].isoformat(),
        "neighbors": neighbor_records,
        "mean": mean,
        "spread": max(all_temps) - min(all_temps),
        "vs_mean": primary_temp - mean,
        "n_stations": len(all_temps),
    }


if __name__ == "__main__":
    import argparse, json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=6)
    ap.add_argument("--show", default=None, help="Print regional_field for this primary")
    args = ap.parse_args()
    n = pull_all_neighbors(args.hours)
    log.info("neighbor pull: %d rows persisted", n)
    if args.show:
        field = regional_field(args.show)
        print(json.dumps(field, indent=2, default=str))
