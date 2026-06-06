"""Compare direct NOAA MADIS HFMETAR against the existing IEM recent feed.

This is a latency/completeness bakeoff, not a production collector. It answers:

    - Does direct MADIS have a fresher latest observation than IEM?
    - Do the latest temperatures agree?
    - Which Kalshi stations are missing from either source?

Usage:
    python -m weather_bot.research.madis_hfmetar_benchmark --stations KLAX,KSFO,KAUS
"""
from __future__ import annotations

import argparse
import gzip
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from weather_bot.config import ACTIVE_TRADE_STATIONS
from weather_bot.data import iem_fetcher


MADIS_HFMETAR_URL = "https://madis-data.ncep.noaa.gov/madisPublic1/data/LDAD/hfmetar/netCDF/"


@dataclass(frozen=True)
class Obs:
    station: str
    obs_time: datetime | None
    temp_f: float | None
    source: str


def _k_to_f(k: float) -> float:
    return (k - 273.15) * 9.0 / 5.0 + 32.0


def _latest_madis_file() -> str | None:
    resp = requests.get(MADIS_HFMETAR_URL, timeout=30, headers={"User-Agent": "weather-bot/0.1"})
    resp.raise_for_status()
    matches = re.findall(r'(\d{8}[_ ]?\d{4})\.gz', resp.text)
    if not matches:
        return None
    matches.sort(reverse=True)
    return f"{matches[0].replace(' ', '_')}.gz"


def _decode_station_id(row) -> str:
    try:
        if hasattr(row, "tobytes"):
            return b"".join(row.tobytes().split(b"\x00")[:1]).decode("ascii", errors="ignore").strip().upper()
    except Exception:
        pass
    return str(row).strip().upper()


def fetch_direct_madis() -> dict[str, Obs]:
    """Return latest direct-MADIS HFMETAR observation per station.

    Requires optional dependency `netCDF4`. The script exits cleanly with an
    actionable message when it is not installed.
    """
    try:
        from netCDF4 import Dataset
    except ImportError as exc:
        raise SystemExit("netCDF4 is required for direct MADIS parsing. Install it only if we choose to operationalize this benchmark.") from exc

    fname = _latest_madis_file()
    if not fname:
        return {}
    resp = requests.get(f"{MADIS_HFMETAR_URL}{fname}", timeout=60, headers={"User-Agent": "weather-bot/0.1"})
    resp.raise_for_status()
    raw = resp.content
    try:
        raw = gzip.decompress(raw)
    except gzip.BadGzipFile:
        pass

    out: dict[str, Obs] = {}
    nc = Dataset(fname, memory=raw)
    try:
        station_ids = nc.variables.get("stationId")
        temps = nc.variables.get("temperature")
        obs_times = nc.variables.get("observationTime")
        if station_ids is None or temps is None:
            return {}
        for idx in range(station_ids.shape[0]):
            station = _decode_station_id(station_ids[idx])
            if len(station) != 4:
                continue
            try:
                temp_k = float(temps[idx])
            except Exception:
                continue
            if not 180 < temp_k < 340:
                continue
            obs_time = None
            if obs_times is not None:
                try:
                    obs_time = datetime.fromtimestamp(float(obs_times[idx]), tz=timezone.utc)
                except Exception:
                    obs_time = None
            obs = Obs(station=station, obs_time=obs_time, temp_f=_k_to_f(temp_k), source=f"MADIS:{fname}")
            prev = out.get(station)
            if prev is None or (obs.obs_time or datetime.min.replace(tzinfo=timezone.utc)) > (prev.obs_time or datetime.min.replace(tzinfo=timezone.utc)):
                out[station] = obs
    finally:
        nc.close()
    return out


def fetch_iem_latest(station: str, hours: int) -> Obs | None:
    rows = [r for r in iem_fetcher.fetch_recent(station, hours=hours) if r.get("temp_f") is not None]
    if not rows:
        return None
    row = max(rows, key=lambda r: r["obs_time"])
    return Obs(station=station, obs_time=row["obs_time"], temp_f=float(row["temp_f"]), source="IEM_RECENT")


def _age_min(obs: Obs | None) -> float | None:
    if obs is None or obs.obs_time is None:
        return None
    return (datetime.now(tz=timezone.utc) - obs.obs_time).total_seconds() / 60.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stations", default=",".join(ACTIVE_TRADE_STATIONS))
    ap.add_argument("--iem-hours", type=int, default=2)
    args = ap.parse_args()

    stations = [s.strip().upper() for s in args.stations.split(",") if s.strip()]
    direct = fetch_direct_madis()
    print(f"{'station':<8}{'madis_age':>10}{'iem_age':>10}{'madis_f':>10}{'iem_f':>10}{'delta':>9}{'verdict':>12}")
    for station in stations:
        madis = direct.get(station)
        iem = fetch_iem_latest(station, args.iem_hours)
        madis_age = _age_min(madis)
        iem_age = _age_min(iem)
        delta = None
        if madis and iem and madis.temp_f is not None and iem.temp_f is not None:
            delta = madis.temp_f - iem.temp_f
        verdict = "OK"
        if madis is None:
            verdict = "NO_MADIS"
        elif iem is None:
            verdict = "NO_IEM"
        elif madis_age is not None and iem_age is not None and madis_age + 1 < iem_age:
            verdict = "MADIS_FASTER"
        elif delta is not None and abs(delta) > 1.1:
            verdict = "TEMP_DIFF"
        print(
            f"{station:<8}"
            f"{madis_age:>10.1f}" if madis_age is not None else f"{station:<8}{'--':>10}",
            end="",
        )
        print(
            f"{iem_age:>10.1f}" if iem_age is not None else f"{'--':>10}",
            f"{madis.temp_f:>10.1f}" if madis and madis.temp_f is not None else f"{'--':>10}",
            f"{iem.temp_f:>10.1f}" if iem and iem.temp_f is not None else f"{'--':>10}",
            f"{delta:>9.1f}" if delta is not None else f"{'--':>9}",
            f"{verdict:>12}",
            sep="",
        )


if __name__ == "__main__":
    main()
