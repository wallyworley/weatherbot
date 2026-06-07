"""Point-in-time center decomposition: NBM-p50 vs GFS vs HRRR vs prod blend.

RESEARCH-ONLY. No trading logic touched. Supports DEFECT_GFS_BLEND.md.

For every event the market-relative benchmark scored (coherent snapshot), this
re-uses the SAME snapshot timestamp as the point-in-time cutoff and pulls, with
`run_time <= snapshot_ts` only:
  - NBM p50 (the production distribution center before any blend)
  - GFS daily TMAX (station-local max of TMP_2M, latest run)
  - HRRR daily TMAX (same)
Each is compared to CLI settlement truth via mean absolute error, segmented by
lead day. This tests whether "GFS beats NBM" survives strict run_time<=as_of
alignment with valid-time aggregation in the station-local day, and whether the
0.30 GFS weight / HRRR blend is justified on these events.

Bias-correction note: production subtracts a station bias from GFS/HRRR before
blending. We report BOTH raw and station-bias-adjusted point errors so the
comparison is not confounded by the bias step.

Usage:
    python -m weather_bot.research.gfs_nbm_pit_center --days 3650
"""
from __future__ import annotations

import argparse
import statistics as st
from collections import defaultdict

from weather_bot.config import STATIONS
from weather_bot.data import persistence
from weather_bot.research.snapshot_market_benchmark import collect_snapshot_bucket_rows


def _events(days: int, max_lead_day: int):
    rows, _ = collect_snapshot_bucket_rows(days, max_lead_day, "TMAX_DAILY", 10, 3)
    ev = {}
    for r in rows:
        key = (r.station, r.valid_date, r.lead_day)
        # snapshot_ts = latest ts among the event's buckets
        if key not in ev or r.ts > ev[key][1]:
            ev[key] = (r.truth_f, r.ts)
    return ev


def run(days: int = 3650, max_lead_day: int = 7) -> dict:
    ev = _events(days, max_lead_day)

    nbm_sql = """
        SELECT value FROM prob_forecast
         WHERE station=%s AND valid_date=%s AND var='TMAX_DAILY' AND percentile=50
           AND run_time <= %s
         ORDER BY run_time DESC LIMIT 1
    """
    det_sql = """
        SELECT MAX(value) AS tmax FROM det_forecast
         WHERE station=%s AND model=%s AND var='TMP_2M'
           AND (valid_time AT TIME ZONE %s)::date=%s
           AND run_time = (SELECT MAX(run_time) FROM det_forecast
                            WHERE station=%s AND model=%s AND var='TMP_2M'
                              AND (valid_time AT TIME ZONE %s)::date=%s
                              AND run_time <= %s)
    """
    bias_sql = """
        SELECT mean_bias_f FROM station_bias
         WHERE station=%s AND model=%s AND var='TMAX_DAILY' AND month=%s AND lead_day=%s
         ORDER BY sample_size DESC LIMIT 1
    """

    # err_by[lead][source] = list of abs errors
    err_raw = defaultdict(lambda: defaultdict(list))
    err_adj = defaultdict(lambda: defaultdict(list))
    n_by_lead = defaultdict(int)

    with persistence.connect() as conn, conn.cursor() as cur:
        def nbm_p50(stn, vd, ts):
            cur.execute(nbm_sql, (stn, vd, ts)); r = cur.fetchone(); return r["value"] if r else None

        def det(stn, model, vd, ts):
            tz = STATIONS[stn].tz
            cur.execute(det_sql, (stn, model, tz, vd, stn, model, tz, vd, ts))
            r = cur.fetchone(); return r["tmax"] if r and r["tmax"] is not None else None

        def bias(stn, model, month, lead):
            cur.execute(bias_sql, (stn, model, month, max(lead, 0)))
            r = cur.fetchone(); return float(r["mean_bias_f"]) if r and r["mean_bias_f"] is not None else 0.0

        for (stn, vd, lead), (truth, ts) in ev.items():
            n_by_lead[lead] += 1
            nbm = nbm_p50(stn, vd, ts)
            gfs = det(stn, "GFS", vd, ts)
            hrrr = det(stn, "HRRR", vd, ts)
            if nbm is not None:
                err_raw[lead]["NBM_p50"].append(abs(nbm - truth))
                err_adj[lead]["NBM_p50"].append(abs(nbm - truth))  # NBM center has no extra bias step here
            if gfs is not None:
                err_raw[lead]["GFS"].append(abs(gfs - truth))
                gb = bias(stn, "GFS", vd.month, lead)
                err_adj[lead]["GFS"].append(abs((gfs - gb) - truth))
            if hrrr is not None:
                err_raw[lead]["HRRR"].append(abs(hrrr - truth))
                hb = bias(stn, "HRRR", vd.month, 0)
                err_adj[lead]["HRRR"].append(abs((hrrr - hb) - truth))
            # production-style blend center (lead>=1: 0.30 GFS shift off NBM median)
            if nbm is not None and gfs is not None:
                gb = bias(stn, "GFS", vd.month, lead)
                blended = nbm + 0.30 * ((gfs - gb) - nbm)
                err_adj[lead]["NBM+0.30GFS"].append(abs(blended - truth))

    def summarize(err):
        out = {}
        for lead in sorted(err):
            out[lead] = {src: {"n": len(v), "mae": round(st.fmean(v), 3)}
                         for src, v in sorted(err[lead].items())}
        return out

    return {"n_events_by_lead": dict(n_by_lead),
            "raw_point_mae": summarize(err_raw),
            "bias_adjusted_point_mae": summarize(err_adj)}


if __name__ == "__main__":
    import json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=3650)
    p.add_argument("--max-lead-day", type=int, default=7)
    a = p.parse_args()
    print(json.dumps(run(a.days, a.max_lead_day), indent=2))
