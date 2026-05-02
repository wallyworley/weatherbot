"""Open-Meteo client for ECMWF + GFS hourly temperature forecasts.

Open-Meteo proxies multiple models with a uniform JSON shape. We use it as
the simplest path to ECMWF (no GRIB parsing) and as parity-source for GFS
(also available via direct NOAA S3, but Open-Meteo gives identical fields
with less code in the research layer).

Endpoints:
    ECMWF: https://api.open-meteo.com/v1/ecmwf  (HRES, 0.4°, 4× daily)
    GFS:   https://api.open-meteo.com/v1/gfs    (0.25°, 4× daily)

Both accept lat/lon + variable list and return hourly arrays. We derive
daily TMAX/TMIN by aggregating hourly temperature_2m over the local day.

Research-layer module — no DB writes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from weather_bot.config import STATIONS

log = logging.getLogger(__name__)

OM_BASE = {
    "ecmwf": "https://api.open-meteo.com/v1/ecmwf",
    "gfs":   "https://api.open-meteo.com/v1/gfs",
}

# For historical (as-issued) forecasts. Different host, unified /v1/forecast
# endpoint, model selected via `models=` param.
OM_HISTORICAL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
OM_HISTORICAL_MODELS = {
    "ecmwf": "ecmwf_ifs025",   # 0.25° IFS — has working hourly archive
    "gfs":   "gfs_seamless",
}


@dataclass
class HourlyForecast:
    model: str
    station: str
    run_time: Optional[datetime]   # not always exposed; falls back to "current_weather" issue if available
    valid_times: list[datetime]    # hourly, station-local timezone-aware
    temp_f: list[float]


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def fetch_hourly(model: str, station: str, start: date, end: date,
                  historical: bool = False) -> HourlyForecast:
    """Pull hourly temperature_2m for the station between [start, end] (local dates).

    historical=True uses Open-Meteo's historical-forecast archive, which serves
    forecasts as-issued at past times (suitable for apples-to-apples backtest
    vs your stored NBM/HRRR history). historical=False uses the current
    forecast endpoint (only useful for today/near-future).
    """
    if model not in OM_BASE:
        raise ValueError(f"unknown model: {model}")
    s = STATIONS[station]
    params = {
        "latitude": s.lat,
        "longitude": s.lon,
        "hourly": "temperature_2m",
        "temperature_unit": "fahrenheit",
        "timezone": s.tz,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    if historical:
        url = OM_HISTORICAL
        params["models"] = OM_HISTORICAL_MODELS[model]
    else:
        url = OM_BASE[model]
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    j = r.json()
    tz = ZoneInfo(s.tz)
    times = [datetime.fromisoformat(t).replace(tzinfo=tz) for t in j["hourly"]["time"]]
    temps = [float(t) if t is not None else float("nan") for t in j["hourly"]["temperature_2m"]]
    return HourlyForecast(model=model, station=station, run_time=None,
                           valid_times=times, temp_f=temps)


def daily_tmax_tmin(fc: HourlyForecast, target_date: date) -> tuple[Optional[float], Optional[float]]:
    """Aggregate the hourly forecast into daily TMAX / TMIN for target_date (local)."""
    matched = [(t, v) for t, v in zip(fc.valid_times, fc.temp_f) if t.date() == target_date and v == v]
    if not matched:
        return None, None
    vals = [v for _, v in matched]
    return max(vals), min(vals)


def fetch_forecast_daily(model: str, station: str, target_date: date,
                          historical: bool = False) -> dict:
    """Convenience: pull hourly for [target_date - 1, target_date + 1] and aggregate."""
    fc = fetch_hourly(model, station, target_date - timedelta(days=1),
                       target_date + timedelta(days=1), historical=historical)
    tmax, tmin = daily_tmax_tmin(fc, target_date)
    return {
        "model": model,
        "station": station,
        "target_date": target_date.isoformat(),
        "tmax_f": tmax,
        "tmin_f": tmin,
        "n_hourly": sum(1 for t in fc.valid_times if t.date() == target_date),
        "historical": historical,
    }


if __name__ == "__main__":
    import argparse, json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", default="KNYC")
    ap.add_argument("--target-date", default=None,
                     help="ISO date (default: today)")
    ap.add_argument("--model", choices=["ecmwf", "gfs", "both"], default="both")
    ap.add_argument("--historical", action="store_true",
                     help="Use historical-forecast archive (forecasts as issued at past times)")
    args = ap.parse_args()
    td = date.fromisoformat(args.target_date) if args.target_date else date.today()

    out: dict = {"station": args.station, "target_date": td.isoformat()}
    models = ["ecmwf", "gfs"] if args.model == "both" else [args.model]
    for m in models:
        out[m] = fetch_forecast_daily(m, args.station, td, historical=args.historical)
    print(json.dumps(out, indent=2, default=str))
