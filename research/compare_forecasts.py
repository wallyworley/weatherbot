"""Compare NBM / HRRR / ECMWF / GFS / WeatherNext forecast accuracy.

For each (station, valid_date) pair in the past N days:
- Pull NBM p50 from prob_forecast (run on valid_date - lead_day)
- Pull HRRR daily TMAX from det_forecast (run on valid_date - lead_day, daily MAX)
- Pull ECMWF/GFS from Open-Meteo historical-forecast-api
- Optionally pull WeatherNext 2 from BigQuery when configured
- Compare each against CLI tmax (settlement authority)

Outputs CSV + markdown summary with MAE / RMSE / signed bias per source.

Caveat: Open-Meteo's historical-forecast-api archives the best-available
forecast for a target date but doesn't expose run_time granularity the way
NBM/HRRR raw GRIB does. So ECMWF/GFS comparisons here approximate
"forecast for this day" rather than precisely "as-issued at lead-1 run".
A direct ECMWF/GFS GRIB pull would be needed for strict run-time parity;
this is good enough for a first-cut viability check.

Question this answers:
    Are ECMWF/GFS competitive with NBM/HRRR on TMAX accuracy?
    If yes, justifies promoting one or both into the live ensemble.
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

from weather_bot.config import ACTIVE_FETCH_STATIONS
from weather_bot.data import persistence

from research.sources.nws_text_products import (
    STATION_TO_LOC, fetch_text_iem, get_product, list_products, parse_cli_yesterday,
)
from research.sources.openmeteo_fetcher import fetch_forecast_daily

log = logging.getLogger(__name__)


@dataclass
class FcRow:
    station: str
    valid_date: date
    lead_day: int
    truth_tmax: Optional[float]      # CLI
    nbm_p50: Optional[float]
    hrrr_tmax: Optional[float]
    ecmwf_tmax: Optional[float]
    gfs_tmax: Optional[float]
    weathernext_tmax_p50: Optional[float] = None
    weathernext_members: int = 0
    weathernext_error: Optional[str] = None


_NWS_ARCHIVE_DAYS = 6


def _truth_cli(station: str, target: date) -> Optional[float]:
    """Pull CLI TMAX for target_date. Tries NWS API (last ~6 days), falls
    back to IEM archive for older history."""
    loc = STATION_TO_LOC.get(station)
    if not loc:
        return None
    if (date.today() - target).days <= _NWS_ARCHIVE_DAYS:
        start = datetime.combine(target + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        end = start + timedelta(hours=18)
        try:
            products = list_products("CLI", loc, start=start, end=end, limit=10)
            if products:
                prod = get_product(products[0]["id"])
                obs = parse_cli_yesterday(prod.text)
                if obs.tmax_f is not None:
                    return obs.tmax_f
        except Exception as e:
            log.warning("NWS truth_cli %s %s: %s", station, target, e)
    iem_text = fetch_text_iem("CLI", station, target)
    if iem_text:
        obs = parse_cli_yesterday(iem_text)
        return obs.tmax_f
    return None


def _nbm_p50(station: str, valid_date: date, lead_day: int) -> Optional[float]:
    """Latest NBM p50 forecast issued on (valid_date - lead_day) for the target."""
    issue_date = valid_date - timedelta(days=lead_day)
    sql = """
        SELECT value
          FROM prob_forecast
         WHERE station=%s AND var='TMAX_DAILY' AND percentile=50
           AND valid_date=%s
           AND run_time::date = %s
         ORDER BY run_time DESC
         LIMIT 1
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, valid_date, issue_date))
        r = cur.fetchone()
    return float(r["value"]) if r and r["value"] is not None else None


def _hrrr_daily_tmax(station: str, valid_date: date, lead_day: int) -> Optional[float]:
    """Daily TMAX from HRRR forecasts: max of valid-time hourly temps in the local day."""
    issue_date = valid_date - timedelta(days=lead_day)
    sql = """
        SELECT MAX(value) AS tmax
          FROM det_forecast
         WHERE station=%s AND model='HRRR' AND var='TMP_2M'
           AND valid_time::date = %s
           AND run_time::date = %s
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, valid_date, issue_date))
        r = cur.fetchone()
    return float(r["tmax"]) if r and r["tmax"] is not None else None


def collect(
    station: str,
    days_back: int,
    lead_day: int,
    *,
    include_weathernext: bool = False,
    weathernext_init_hour: int = 0,
) -> list[FcRow]:
    rows: list[FcRow] = []
    today = date.today()
    for i in range(1, days_back + 1):
        d = today - timedelta(days=i)
        log.info("collect %s %s lead=%d", station, d, lead_day)
        truth = _truth_cli(station, d)
        nbm = _nbm_p50(station, d, lead_day)
        hrrr = _hrrr_daily_tmax(station, d, lead_day)
        try:
            ec = fetch_forecast_daily("ecmwf", station, d, historical=True)
            ec_tmax = ec.get("tmax_f")
        except Exception as e:
            log.warning("ecmwf %s %s: %s", station, d, e)
            ec_tmax = None
        try:
            gf = fetch_forecast_daily("gfs", station, d, historical=True)
            gf_tmax = gf.get("tmax_f")
        except Exception as e:
            log.warning("gfs %s %s: %s", station, d, e)
            gf_tmax = None

        wn_tmax = None
        wn_members = 0
        wn_error = None
        if include_weathernext:
            from research.sources import weathernext_fetcher

            init_time = datetime.combine(
                d - timedelta(days=lead_day),
                time(hour=weathernext_init_hour),
                tzinfo=timezone.utc,
            )
            wn = weathernext_fetcher.fetch_forecast_daily(
                station,
                d,
                init_time=init_time,
                fail_soft=True,
            )
            wn_tmax = wn.get("tmax_p50_f")
            wn_members = int(wn.get("members") or 0)
            wn_error = wn.get("error")
            if wn_error:
                log.warning("weathernext %s %s: %s", station, d, wn_error)

        rows.append(FcRow(station=station, valid_date=d, lead_day=lead_day,
                           truth_tmax=truth, nbm_p50=nbm, hrrr_tmax=hrrr,
                           ecmwf_tmax=ec_tmax, gfs_tmax=gf_tmax,
                           weathernext_tmax_p50=wn_tmax,
                           weathernext_members=wn_members,
                           weathernext_error=wn_error))
    return rows


def write_csv(rows: list[FcRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))


def _errors(rows: list[FcRow], pred_attr: str) -> dict:
    """Return {n, mae, rmse, bias} comparing rows[*].pred_attr to rows[*].truth_tmax."""
    pairs = [(getattr(r, pred_attr), r.truth_tmax) for r in rows
             if getattr(r, pred_attr) is not None and r.truth_tmax is not None]
    if not pairs:
        return {"n": 0, "mae": None, "rmse": None, "bias": None}
    diffs = [p - t for p, t in pairs]
    return {
        "n": len(pairs),
        "mae":  statistics.fmean(abs(d) for d in diffs),
        "rmse": math.sqrt(statistics.fmean(d * d for d in diffs)),
        "bias": statistics.fmean(diffs),   # +ve = over-forecast
    }


def summarize(rows: list[FcRow]) -> str:
    by_station: dict[str, list[FcRow]] = {}
    for r in rows:
        by_station.setdefault(r.station, []).append(r)

    lines = ["# Forecast Source Comparison (TMAX_DAILY)\n",
             f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_\n",
             "Compares NBM (p50) / HRRR (daily max) / ECMWF / GFS / WeatherNext "
             "predictions "
             "against CLI ground truth.\n",
             "MAE/RMSE in °F. Bias > 0 means the source forecast warmer than reality.\n"]

    for st, st_rows in by_station.items():
        lead_days = sorted({r.lead_day for r in st_rows})
        for lead in lead_days:
            sub = [r for r in st_rows if r.lead_day == lead]
            lines.append(f"\n## {st} — lead day {lead}\n")
            lines.append(f"days surveyed: {len(sub)}\n")
            lines.append("| source | n | MAE | RMSE | bias |")
            lines.append("|---|---:|---:|---:|---:|")
            for src, attr in [("NBM p50", "nbm_p50"),
                                ("HRRR",    "hrrr_tmax"),
                                ("ECMWF",   "ecmwf_tmax"),
                                ("GFS",     "gfs_tmax"),
                                ("WeatherNext2 p50", "weathernext_tmax_p50")]:
                e = _errors(sub, attr)
                if e["n"] == 0:
                    lines.append(f"| {src} | 0 | — | — | — |")
                else:
                    lines.append(f"| {src} | {e['n']} | {e['mae']:.2f} | {e['rmse']:.2f} | {e['bias']:+.2f} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", nargs="+", default=ACTIVE_FETCH_STATIONS)
    ap.add_argument("--days-back", type=int, default=30)
    ap.add_argument("--lead-days", type=int, nargs="+", default=[1],
                     help="lead day(s) to compare; 1 = day-ahead, 0 = same-day intraday")
    ap.add_argument("--out-dir", default="research/reports")
    ap.add_argument("--include-weathernext", action="store_true",
                    help="Include WeatherNext 2 BigQuery data when configured.")
    ap.add_argument("--weathernext-init-hour", type=int, default=0,
                    choices=[0, 6, 12, 18],
                    help="WeatherNext init hour UTC for the issue date.")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    all_rows: list[FcRow] = []
    for st in args.stations:
        for ld in args.lead_days:
            all_rows.extend(
                collect(
                    st,
                    args.days_back,
                    ld,
                    include_weathernext=args.include_weathernext,
                    weathernext_init_hour=args.weathernext_init_hour,
                )
            )

    csv_path = out_dir / f"fc_compare_{date.today()}.csv"
    md_path = out_dir / f"fc_compare_{date.today()}.md"
    write_csv(all_rows, csv_path)
    md_path.write_text(summarize(all_rows))
    log.info("wrote %s and %s", csv_path, md_path)
    print(md_path.read_text())
