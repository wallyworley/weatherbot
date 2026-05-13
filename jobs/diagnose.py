"""
Diagnostic CLI — inspects the state of the bot's tables and forecast quality.

Usage:
    python -m weather_bot.jobs.diagnose
    python -m weather_bot.jobs.diagnose --station KNYC --var TMAX_DAILY --days 14

Checks:
    1. Row counts in each table
    2. station_bias contents (bias-correction training state)
    3. Per-day forecast vs obs table for the last N days (p10/p50/p90)
    4. Rough forecast-quality summary (mean abs error, within-envelope rate)
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone

from weather_bot.data import persistence


def _print_header(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _row_counts(cur) -> None:
    _print_header("TABLE ROW COUNTS")
    tables = [
        "stations", "metar_obs", "daily_obs",
        "det_forecast", "prob_forecast",
        "station_bias", "verification",
    ]
    for t in tables:
        try:
            cur.execute(f"SELECT COUNT(*) AS n FROM {t}")
            n = cur.fetchone()["n"]
            print(f"  {t:<20} {n:>8}")
        except Exception as exc:
            print(f"  {t:<20}   ERROR: {exc}")


def _obs_spread(cur, station: str) -> None:
    _print_header(f"OBS SPREAD — {station}")
    cur.execute(
        """
        SELECT COUNT(*) AS n,
               MIN(obs_time)::date AS min_d,
               MAX(obs_time)::date AS max_d
        FROM metar_obs WHERE station = %s
        """,
        (station,),
    )
    r = cur.fetchone()
    print(f"  metar_obs : n={r['n']}  range={r['min_d']} → {r['max_d']}")

    cur.execute(
        """
        SELECT COUNT(*) AS n,
               MIN(local_date) AS min_d,
               MAX(local_date) AS max_d
        FROM daily_obs WHERE station = %s
        """,
        (station,),
    )
    r = cur.fetchone()
    print(f"  daily_obs : n={r['n']}  range={r['min_d']} → {r['max_d']}")


def _station_bias(cur, station: str) -> None:
    _print_header(f"STATION_BIAS — {station}")
    cur.execute(
        """
        SELECT model, var, month, lead_day, mean_bias_f, stddev_f, sample_size
        FROM station_bias
        WHERE station = %s
        ORDER BY model, var, month, lead_day
        """,
        (station,),
    )
    rows = cur.fetchall()
    if not rows:
        print("  (empty — bias correction has no training rows)")
        return
    print(f"  {'model':<6} {'var':<12} {'month':>5} {'lead':>4} "
          f"{'mean_bias':>10} {'stddev':>8} {'n':>5}")
    for r in rows:
        print(
            f"  {r['model']:<6} {r['var']:<12} {r['month']:>5} {r['lead_day']:>4} "
            f"{r['mean_bias_f']:>+10.2f} {r['stddev_f']:>8.2f} {r['sample_size']:>5}"
        )


def _forecast_vs_obs(cur, station: str, var: str, days: int) -> None:
    """Show raw NBM p50 AND the bias-corrected distribution next to obs."""
    from datetime import datetime, timezone
    from weather_bot.models.distribution import build_station_distribution

    _print_header(f"FORECAST vs OBS — {station} {var}  last {days} days")
    obs_col = "tmax_f" if var == "TMAX_DAILY" else "tmin_f"
    start = date.today() - timedelta(days=days)

    # Pull raw NBM p10/p50/p90 for reference.
    cur.execute(
        f"""
        WITH latest AS (
          SELECT DISTINCT ON (station, valid_date, var, percentile)
                 station, valid_date, var, percentile, value, run_time
          FROM prob_forecast
          WHERE station = %s AND var = %s
          ORDER BY station, valid_date, var, percentile, run_time DESC
        )
        SELECT o.local_date,
               o.{obs_col}        AS obs,
               p10.value          AS raw_p10,
               p50.value          AS raw_p50,
               p90.value          AS raw_p90
        FROM daily_obs o
        LEFT JOIN latest p10 ON p10.station=o.station AND p10.valid_date=o.local_date AND p10.percentile=10
        LEFT JOIN latest p50 ON p50.station=o.station AND p50.valid_date=o.local_date AND p50.percentile=50
        LEFT JOIN latest p90 ON p90.station=o.station AND p90.valid_date=o.local_date AND p90.percentile=90
        WHERE o.station = %s AND o.local_date >= %s
        ORDER BY o.local_date
        """,
        (station, var, station, start),
    )
    rows = cur.fetchall()
    if not rows:
        print("  (no overlap between daily_obs and prob_forecast in this window)")
        return

    print(f"  {'date':<12} {'obs':>6}  {'raw_p50':>7}  "
          f"{'cor_p10':>7} {'cor_p50':>7} {'cor_p90':>7}  {'err_raw':>7} {'err_cor':>7} {'in_env':>7}")

    raw_errs: list[float] = []
    cor_errs: list[float] = []
    in_env = 0
    in_env_total = 0

    for r in rows:
        obs = r["obs"]
        raw_p50 = r["raw_p50"]

        # Build the bias-corrected distribution as-of station-local midnight.
        from weather_bot.config import STATIONS
        import pytz
        tz = pytz.timezone(STATIONS[station].tz)
        local_midnight = tz.localize(datetime.combine(r["local_date"], datetime.min.time()))
        cdf = build_station_distribution(
            station, r["local_date"], var=var,
            now_utc=local_midnight.astimezone(timezone.utc),
        )

        def _fmt(x):
            return f"{x:>7.1f}" if x is not None else "    n/a"

        if cdf is not None:
            cor_p10 = cdf.quantile(0.10)
            cor_p50 = cdf.quantile(0.50)
            cor_p90 = cdf.quantile(0.90)
        else:
            cor_p10 = cor_p50 = cor_p90 = None

        err_raw_txt = "      -"
        if obs is not None and raw_p50 is not None:
            e = float(raw_p50) - float(obs)
            raw_errs.append(e)
            err_raw_txt = f"{e:>+7.2f}"

        err_cor_txt = "      -"
        if obs is not None and cor_p50 is not None:
            e = float(cor_p50) - float(obs)
            cor_errs.append(e)
            err_cor_txt = f"{e:>+7.2f}"

        env_txt = "   -"
        if obs is not None and cor_p10 is not None and cor_p90 is not None:
            in_env_total += 1
            hit = cor_p10 <= obs <= cor_p90
            if hit:
                in_env += 1
            env_txt = "    yes" if hit else "     NO"

        print(
            f"  {str(r['local_date']):<12} {obs:>6.1f}  {_fmt(raw_p50)}  "
            f"{_fmt(cor_p10)} {_fmt(cor_p50)} {_fmt(cor_p90)}  "
            f"{err_raw_txt} {err_cor_txt} {env_txt}"
        )

    import statistics
    if raw_errs:
        print()
        print(f"  RAW        p50 bias: {statistics.mean(raw_errs):+6.2f}°F  "
              f"MAE: {statistics.mean(abs(e) for e in raw_errs):5.2f}°F  "
              f"std: {statistics.pstdev(raw_errs):5.2f}°F")
    if cor_errs:
        print(f"  CORRECTED  p50 bias: {statistics.mean(cor_errs):+6.2f}°F  "
              f"MAE: {statistics.mean(abs(e) for e in cor_errs):5.2f}°F  "
              f"std: {statistics.pstdev(cor_errs):5.2f}°F")
    if in_env_total:
        print(f"  obs inside corrected p10-p90: {in_env}/{in_env_total} "
              f"({100*in_env/in_env_total:.0f}%)  [expect ~80%]")


def _verification_latest(cur, station: str) -> None:
    _print_header(f"VERIFICATION — latest per-var, {station}")
    cur.execute(
        """
        SELECT DISTINCT ON (station, var, lead_day)
               run_date, var, lead_day, brier, crps, log_loss, n
        FROM verification
        WHERE station = %s
        ORDER BY station, var, lead_day, run_date DESC
        """,
        (station,),
    )
    rows = cur.fetchall()
    if not rows:
        print("  (no verification rows)")
        return
    print(f"  {'run_date':<12} {'var':<12} {'lead':>4} {'brier':>7} {'crps':>7} {'logloss':>8} {'n':>4}")
    for r in rows:
        print(
            f"  {str(r['run_date']):<12} {r['var']:<12} {r['lead_day']:>4} "
            f"{r['brier']:>7.4f} {r['crps']:>7.3f} {r['log_loss']:>8.3f} {r['n']:>4}"
        )


def _purge_incomplete_today(cur) -> None:
    """Delete daily_obs rows for calendar days that aren't complete yet (local tz).

    These get auto-generated by backfills before the day is over and corrupt
    both the bias-training set and verification metrics.
    """
    from weather_bot.config import STATIONS
    import pytz
    now_utc = datetime.now(tz=timezone.utc)
    deleted = 0
    for code, st in STATIONS.items():
        tz = pytz.timezone(st.tz)
        today_local = now_utc.astimezone(tz).date()
        # If the local day hasn't ended (in UTC terms), it's incomplete.
        local_end_utc = tz.localize(datetime.combine(today_local + timedelta(days=1),
                                                      datetime.min.time())).astimezone(timezone.utc)
        if local_end_utc > now_utc:
            cur.execute(
                "DELETE FROM daily_obs WHERE station=%s AND local_date=%s",
                (code, today_local),
            )
            deleted += cur.rowcount or 0
    print(f"  purged {deleted} incomplete-day daily_obs rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", default="KNYC")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--var", default="both", choices=["TMAX_DAILY", "TMIN_DAILY", "both"])
    ap.add_argument("--purge-today", action="store_true",
                    help="Delete daily_obs rows for days that aren't complete yet")
    args = ap.parse_args()

    with persistence.connect() as conn, conn.cursor() as cur:
        if args.purge_today:
            _print_header("PURGE INCOMPLETE DAYS")
            _purge_incomplete_today(cur)
            conn.commit()

        _row_counts(cur)
        _obs_spread(cur, args.station)
        _station_bias(cur, args.station)
        _verification_latest(cur, args.station)

        vars_to_check = (
            ["TMAX_DAILY", "TMIN_DAILY"] if args.var == "both" else [args.var]
        )
        for v in vars_to_check:
            _forecast_vs_obs(cur, args.station, v, args.days)


if __name__ == "__main__":
    main()
