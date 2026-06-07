"""METAR-vs-CLI intraday-floor basis diagnostic.

RESEARCH-ONLY. Does not modify trading logic. Supports DEFECT_METAR_CLI_FLOOR.md.

Production same-day TMAX distributions truncate P(TMAX < floor) = 0 where
`floor = MAX(metar_obs.temp_f)` for the station-local day up to the signal time
(models/distribution.py:_intraday_bounds / build_station_distribution). Kalshi
settles on the NWS CLI daily max. METAR peak temperatures and CLI daily maxima
differ (sensor quantization, 5-min peak capture, QC). When the METAR floor
lands ABOVE the eventual CLI max, the conditioning zeroes the bucket that
actually settles, manufacturing a confident-wrong distribution.

This diagnostic reconstructs, for each scored lead-0 event at its coherent
snapshot time, the floor that would have applied and measures:
  - floor > CLI truth (floor above settlement)
  - floor >= winner upper edge (floor zeroes the winning bucket)
  - model probability mass placed on the winning bucket in those cases
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from weather_bot.data import persistence
from weather_bot.research.snapshot_market_benchmark import collect_snapshot_bucket_rows


def _metar_max_so_far(station: str, local_date: date, as_of) -> float | None:
    sql = """
    SELECT MAX(mo.temp_f) AS m
      FROM metar_obs mo
      JOIN stations st ON st.code = mo.station
     WHERE mo.station = %s
       AND (mo.obs_time AT TIME ZONE st.tz)::date = %s
       AND mo.obs_time <= %s
       AND mo.temp_f IS NOT NULL
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, local_date, as_of))
        row = cur.fetchone()
        return row["m"] if row and row["m"] is not None else None


def run(days: int = 3650) -> dict:
    rows, _ = collect_snapshot_bucket_rows(days, max_lead_day=0, var="TMAX_DAILY",
                                           tick_minutes=10, min_buckets=3)
    events: dict[tuple, list] = defaultdict(list)
    for r in rows:
        events[(r.station, r.valid_date)].append(r)

    n = 0
    floor_present = 0
    floor_gt_truth = 0
    floor_ge_winner_upper = 0
    winner_zeroed_by_floor = 0
    model_p_on_winner_when_floor_high: list[float] = []
    market_p_on_winner_when_floor_high: list[float] = []
    examples = []

    for (station, vdate), brows in events.items():
        brows = sorted(brows, key=lambda r: (float("-inf") if r.lower_f is None else r.lower_f))
        winners = [r for r in brows if r.yes_win == 1]
        if len(winners) != 1:
            continue
        n += 1
        snapshot_ts = max(r.ts for r in brows)
        truth = brows[0].truth_f
        floor = _metar_max_so_far(station, vdate, snapshot_ts)
        if floor is None:
            continue
        floor_present += 1
        win = winners[0]
        total_model = sum(max(0.0, min(1.0, r.model_p)) for r in brows) or 1.0
        total_market = sum(max(0.0, min(1.0, r.market_p)) for r in brows) or 1.0
        win_model_norm = max(0.0, min(1.0, win.model_p)) / total_model
        win_market_norm = max(0.0, min(1.0, win.market_p)) / total_market

        if floor > truth + 1e-9:
            floor_gt_truth += 1
        if win.upper_f is not None and floor >= win.upper_f - 1e-9:
            floor_ge_winner_upper += 1
            # The winning bucket is entirely below the floor -> truncated to 0.
            winner_zeroed_by_floor += 1
            model_p_on_winner_when_floor_high.append(win_model_norm)
            market_p_on_winner_when_floor_high.append(win_market_norm)
            if len(examples) < 12:
                examples.append(
                    dict(station=station, date=str(vdate), floor=round(floor, 1),
                         truth=round(truth, 1),
                         winner=f"[{win.lower_f},{win.upper_f})",
                         model_p_win=round(win_model_norm, 3),
                         market_p_win=round(win_market_norm, 3))
                )

    return dict(
        events_scored=n,
        events_with_floor=floor_present,
        floor_gt_truth=floor_gt_truth,
        floor_gt_truth_pct=round(100 * floor_gt_truth / floor_present, 1) if floor_present else None,
        winner_zeroed_by_floor=winner_zeroed_by_floor,
        winner_zeroed_pct=round(100 * winner_zeroed_by_floor / floor_present, 1) if floor_present else None,
        mean_model_p_on_zeroed_winner=(
            round(sum(model_p_on_winner_when_floor_high) / len(model_p_on_winner_when_floor_high), 3)
            if model_p_on_winner_when_floor_high else None),
        mean_market_p_on_zeroed_winner=(
            round(sum(market_p_on_winner_when_floor_high) / len(market_p_on_winner_when_floor_high), 3)
            if market_p_on_winner_when_floor_high else None),
        examples=examples,
    )


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
