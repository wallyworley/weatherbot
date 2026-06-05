"""Official NWS/NCEP guidance collector for the alpha research lane.

Adds five externally sourced inputs without changing live trading behavior:

1. NWS_GRID    api.weather.gov forecastGridData / NDFD-style maxTemperature
2. NWS_PFM     Point Forecast Matrix MX/MN text product
3. LAMP        GFS LAMP station hourly temperature guidance
4. MAV         GFS MOS station hourly temperature guidance
5. OBS_TRACKER high-so-far observation context from the existing METAR store

Rows land in forecast_guidance and are consumed by research ablations as
alternate meteorological centers.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable

import pytz
import requests

from weather_bot.config import ACTIVE_FETCH_STATIONS, NEIGHBOR_STATIONS, STATIONS, Station
from weather_bot.data import persistence

log = logging.getLogger(__name__)

NWS_API = "https://api.weather.gov"
USER_AGENT = "weather_bot/0.1 (official-guidance research; contact: local)"

NOMADS_HTTPS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com"


@dataclass(frozen=True)
class ProductText:
    text: str
    run_time: datetime
    url: str


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}


def station_lookup(include_neighbors: bool = True) -> dict[str, Station]:
    """Return configured primary/fetch stations plus optional neighbors."""
    out = dict(STATIONS)
    if include_neighbors:
        for neighbors in NEIGHBOR_STATIONS.values():
            for station in neighbors:
                out.setdefault(station.code, station)
    return out


def default_station_codes(include_neighbors: bool = True) -> list[str]:
    """Default guidance universe, preserving configured order.

    Primaries/fetch-only stations come first. Neighbor stations are appended
    once, for research/regime context only.
    """
    codes = list(ACTIVE_FETCH_STATIONS)
    seen = set(codes)
    if include_neighbors:
        for neighbors in NEIGHBOR_STATIONS.values():
            for station in neighbors:
                if station.code not in seen:
                    codes.append(station.code)
                    seen.add(station.code)
    return codes


def _utc_now_hour() -> datetime:
    return datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)


def _local_noon_for_station(station: Station, d: date) -> datetime:
    tz = pytz.timezone(station.tz)
    return tz.localize(datetime.combine(d, time(12, 0))).astimezone(timezone.utc)


def _valid_date(station: str, dt: datetime) -> date:
    return dt.astimezone(pytz.timezone(STATIONS[station].tz)).date()


def _valid_date_for_tz(tz_name: str, dt: datetime) -> date:
    return dt.astimezone(pytz.timezone(tz_name)).date()


def _lead_hr(run_time: datetime, valid_time: datetime) -> int:
    return int((valid_time - run_time).total_seconds() // 3600)


def _to_f(value: float | int | None, unit_code: str | None = None) -> float | None:
    if value is None:
        return None
    v = float(value)
    unit = unit_code or ""
    if unit.endswith("degC"):
        return v * 9.0 / 5.0 + 32.0
    if unit.endswith("degF") or unit.endswith("degree_Fahrenheit"):
        return v
    return v


def _parse_time_start(valid_time: str) -> datetime | None:
    if not valid_time:
        return None
    start = valid_time.split("/", 1)[0]
    try:
        return datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _point_metadata(station: Station) -> dict:
    r = requests.get(
        f"{NWS_API}/points/{station.lat:.4f},{station.lon:.4f}",
        headers=_headers(),
        timeout=25,
    )
    r.raise_for_status()
    return r.json().get("properties", {})


def fetch_nws_grid(station: Station, run_time: datetime | None = None) -> list[dict]:
    """Fetch NWS forecastGridData for a station and parse TMAX + hourly temp."""
    meta = _point_metadata(station)
    grid_url = meta.get("forecastGridData")
    if not grid_url:
        return []
    r = requests.get(grid_url, headers=_headers(), timeout=30)
    r.raise_for_status()
    payload = r.json()
    props = payload.get("properties", {})
    issued = props.get("updateTime") or props.get("generatedAt")
    if issued:
        try:
            run = datetime.fromisoformat(issued.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            run = run_time or _utc_now_hour()
    else:
        run = run_time or _utc_now_hour()

    rows: list[dict] = []

    def add_series(layer: str, var: str) -> None:
        block = props.get(layer) or {}
        unit = block.get("uom") or block.get("unitCode")
        for item in block.get("values") or []:
            valid_time = _parse_time_start(item.get("validTime") or "")
            value = _to_f(item.get("value"), unit)
            if valid_time is None or value is None:
                continue
            rows.append({
                "station": station.code,
                "source": "NWS_GRID",
                "run_time": run,
                "valid_time": valid_time,
                "valid_date": _valid_date_for_tz(station.tz, valid_time),
                "lead_hr": _lead_hr(run, valid_time),
                "var": var,
                "value": value,
                "units": "degF",
                "raw": {
                    "layer": layer,
                    "gridId": props.get("gridId"),
                    "gridX": props.get("gridX"),
                    "gridY": props.get("gridY"),
                    "validTime": item.get("validTime"),
                },
            })

    add_series("maxTemperature", "TMAX_DAILY")
    add_series("temperature", "TMP_2M")
    return rows


def _nws_products(type_: str, location: str, limit: int = 5) -> list[dict]:
    return _nws_products_window(type_, location, limit=limit)


def _nws_products_window(
    type_: str,
    location: str,
    limit: int = 50,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict]:
    params: dict[str, object] = {"type": type_, "location": location, "limit": limit}
    if start is not None:
        params["start"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    if end is not None:
        params["end"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(
        f"{NWS_API}/products",
        params=params,
        headers=_headers(),
        timeout=25,
    )
    r.raise_for_status()
    return r.json().get("@graph", [])


def _nws_product_text(product_id: str) -> tuple[str, datetime]:
    r = requests.get(f"{NWS_API}/products/{product_id}", headers=_headers(), timeout=25)
    r.raise_for_status()
    j = r.json()
    issued = datetime.fromisoformat(j["issuanceTime"].replace("Z", "+00:00")).astimezone(timezone.utc)
    return j.get("productText", ""), issued


def _aliases(station: Station) -> set[str]:
    name = station.name.upper()
    words = re.sub(r"[^A-Z0-9 ]", " ", name).split()
    aliases = {station.code.upper(), station.code[1:].upper(), name}
    if words:
        aliases.add(" ".join(words[:2]))
        aliases.add(words[0])
    replacements = {
        "INTL": "INTERNATIONAL",
        "NATL": "NATIONAL",
        "BERGSTROM": "BERGSTROM",
        "HARTSFIELD": "HARTSFIELD",
        "SKY": "SKY",
        "MIDWAY": "MIDWAY",
        "REAGAN": "REAGAN",
    }
    for old, new in replacements.items():
        if old in words:
            aliases.add(name.replace(old, new))
    return {a for a in aliases if len(a) >= 3}


def _select_pfm_block(text: str, station: Station) -> str | None:
    blocks = re.split(r"\n\$\$\s*\n?", text)
    aliases = _aliases(station)
    for block in blocks:
        head = "\n".join(block.splitlines()[:8]).upper()
        if any(alias in head for alias in aliases):
            return block
    return None


def parse_pfm_mxmn(text: str, station: Station, issued: datetime) -> list[dict]:
    """Best-effort MX/MN parser for one station's PFM block.

    PFM formats vary by WFO. We use it as a candidate center, so failures should
    skip rows rather than invent alignment. The parser assigns alternating
    MX/MN values to local dates from the issuance context.
    """
    block = _select_pfm_block(text, station)
    if not block:
        return []
    issued_local = issued.astimezone(pytz.timezone(station.tz))
    first_max_date = issued_local.date() if issued_local.hour < 12 else issued_local.date() + timedelta(days=1)
    rows: list[dict] = []
    max_index = 0
    min_index = 0
    guidance_lines = []
    for line in block.splitlines():
        label = line.strip().split(maxsplit=1)[0].upper() if line.strip() else ""
        if label in {"MX/MN", "MIN/MAX", "MAX/MIN"}:
            guidance_lines.append((label, line))
    for label, line in guidance_lines:
        rest = re.sub(r"^\s*(?:MX/MN|MIN/MAX|MAX/MIN)\s*", "", line, flags=re.IGNORECASE)
        values: list[int] = []
        for token in re.findall(r"-?\d+|M", rest):
            if token == "M":
                continue
            try:
                values.append(int(token))
            except ValueError:
                continue
        start_is_max = label in {"MX/MN", "MAX/MIN"}
        for i, value in enumerate(values):
            is_max = (i % 2 == 0) if start_is_max else (i % 2 == 1)
            if is_max:
                d = first_max_date + timedelta(days=max_index)
                max_index += 1
                var = "TMAX_DAILY"
            else:
                d = first_max_date + timedelta(days=min_index)
                min_index += 1
                var = "TMIN_DAILY"
            vt = _local_noon_for_station(station, d)
            rows.append({
                "station": station.code,
                "source": "NWS_PFM",
                "run_time": issued,
                "valid_time": vt,
                "valid_date": d,
                "lead_hr": _lead_hr(issued, vt),
                "var": var,
                "value": float(value),
                "units": "degF",
                "raw": {"parser": label, "line": line.strip()},
            })
    return rows


def fetch_pfm(station: Station) -> list[dict]:
    meta = _point_metadata(station)
    location = meta.get("gridId")
    if not location:
        return []
    products = _nws_products("PFM", location, limit=4)
    for product in products:
        try:
            text, issued = _nws_product_text(product["id"])
        except Exception as exc:
            log.warning("PFM fetch %s failed: %s", product.get("id"), exc)
            continue
        rows = parse_pfm_mxmn(text, station, issued)
        if rows:
            return rows
    return []


def fetch_pfm_history(station: Station, start: datetime, end: datetime) -> list[dict]:
    """Fetch recent PFM products from api.weather.gov and parse station rows."""
    meta = _point_metadata(station)
    location = meta.get("gridId")
    if not location:
        return []
    rows: list[dict] = []
    products = _nws_products_window("PFM", location, limit=200, start=start, end=end)
    for product in products:
        try:
            text, issued = _nws_product_text(product["id"])
        except Exception as exc:
            log.warning("PFM history fetch %s failed: %s", product.get("id"), exc)
            continue
        rows.extend(parse_pfm_mxmn(text, station, issued))
    return rows


def _model_cycle(now: datetime, cadence_hours: int) -> datetime:
    now = now.astimezone(timezone.utc)
    hour = (now.hour // cadence_hours) * cadence_hours
    cycle = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if cycle > now - timedelta(hours=1):
        cycle -= timedelta(hours=cadence_hours)
    return cycle


def _candidate_cycles(source: str, run_time: datetime | None = None) -> list[datetime]:
    if run_time is not None:
        return [run_time]
    now = datetime.now(tz=timezone.utc)
    if source == "LAMP":
        # Full LAMP alphanumeric temperature bulletins are on the :30 cycles;
        # :00 files are commonly flight-category-only and lack TMP rows.
        base = now.replace(second=0, microsecond=0)
        if base.minute >= 30:
            base = base.replace(minute=30)
        else:
            base = (base - timedelta(hours=1)).replace(minute=30)
        return [base - timedelta(hours=i) for i in range(0, 6)]
    base = _model_cycle(now, 6)
    return [base - timedelta(hours=6 * i) for i in range(0, 4)]


def _text_urls(source: str, run: datetime) -> list[str]:
    ymd = run.strftime("%Y%m%d")
    cc = run.strftime("%H")
    if source == "LAMP":
        mm = run.strftime("%M")
        paths = [
            f"lmp/prod/lmp.{ymd}/lmp.t{cc}{mm}z.lavtxt.ascii",
            f"lmp/prod/lmp.{ymd}/lmp.t{cc}{mm}z.lavtxt_ext.ascii",
        ]
    elif source == "MAV":
        paths = [f"gfs_mos/prod/gfs_mos.{ymd}/mdl_gfsmav.t{cc}z"]
    else:
        raise ValueError(source)
    return [f"{NOMADS_HTTPS}/{path}" for path in paths]


def fetch_text_product(source: str, run_time: datetime | None = None) -> ProductText | None:
    for run in _candidate_cycles(source, run_time):
        for url in _text_urls(source, run):
            try:
                r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
                if r.status_code == 200 and len(r.text) > 500:
                    return ProductText(r.text, run, url)
                log.debug("%s text candidate failed %s status=%s len=%d", source, url, r.status_code, len(r.content))
            except Exception as exc:
                log.debug("%s text candidate failed %s: %s", source, url, exc)
    return None


def _lamp_history_cycles(start: datetime, end: datetime) -> list[datetime]:
    start = start.astimezone(timezone.utc).replace(second=0, microsecond=0)
    end = end.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if start.minute <= 30:
        cur = start.replace(minute=30)
    else:
        cur = (start + timedelta(hours=1)).replace(minute=30)
    cycles = []
    while cur <= end:
        cycles.append(cur)
        cur += timedelta(hours=1)
    return cycles


def _mav_history_cycles(start: datetime, end: datetime) -> list[datetime]:
    start = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cur = start.replace(hour=(start.hour // 6) * 6)
    cycles = []
    while cur <= end:
        cycles.append(cur)
        cur += timedelta(hours=6)
    return cycles


def _station_text_block(text: str, station: Station, source: str) -> str | None:
    ids = {station.code.upper(), station.code[1:].upper()}
    marker = "LAMP" if source == "LAMP" else "MOS"
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        up = line.upper()
        if any(re.match(rf"^\s*{re.escape(sid)}\s+", up) for sid in ids) and marker in up:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    next_station = re.compile(r"^\s*[A-Z0-9]{3,4}\s+.*(?:LAMP|MOS)")
    for i in range(start + 1, len(lines)):
        if next_station.match(lines[i].upper()):
            end = i
            break
    return "\n".join(lines[start:end])


def _row_ints(line: str, expected: int | None = None) -> list[int]:
    """Parse a MOS/LAMP row.

    MOS rows are fixed-width after the label. Hot stations can have 3-digit
    temperatures, so values may appear visually concatenated:
    ``TMP  99106107101`` means 99, 106, 107, 101. Regex tokenization would turn
    that into one absurd integer. Prefer 3-character fields when we know how
    many values the row should carry or when a token is suspiciously long.
    """
    tokens = re.findall(r"-?\d+|M", line)
    use_fixed = (
        expected is not None
        or any(len(token.lstrip("-")) > 3 for token in tokens if token != "M")
    )
    if use_fixed and len(line) > 4:
        if expected is not None and len(line) >= expected * 3:
            body = line[-expected * 3:]
        else:
            m = re.match(r"^\s*\S+\s(.*)$", line)
            body = m.group(1) if m else line[4:]
        values: list[int] = []
        for i in range(0, len(body), 3):
            token = body[i:i + 3].strip()
            if not token or token == "M":
                continue
            try:
                values.append(int(token))
            except ValueError:
                continue
        if expected is None or len(values) >= expected:
            return values[:expected] if expected is not None else values
    return [int(x) for x in re.findall(r"-?\d+", line)]


def _row_value_spans(anchor_line: str) -> list[tuple[int, int]]:
    """Return fixed-width value spans implied by a MOS/LAMP hour row."""
    spans = [m.span() for m in re.finditer(r"\d+", anchor_line)]
    starts = [max(0, start - 1) for start, _ in spans]
    out: list[tuple[int, int]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else start + 3
        out.append((start, end))
    return out


def _row_ints_at_spans(line: str, spans: list[tuple[int, int]]) -> list[int]:
    values: list[int] = []
    for start, end in spans:
        token = line[start:end].strip()
        if not token or token == "M":
            continue
        try:
            values.append(int(token))
        except ValueError:
            values.append(int(token[-3:]))
    return values


def parse_hourly_temp_guidance(
    text: str,
    station: Station,
    source: str,
    run_time: datetime,
) -> list[dict]:
    """Parse LAMP/MAV station text into hourly TMP_2M rows."""
    block = _station_text_block(text, station, source)
    if not block:
        return []
    hours: list[int] = []
    hour_spans: list[tuple[int, int]] = []
    temps: list[int] = []
    for line in block.splitlines():
        parts = line.strip().split()
        label = parts[0].upper() if parts else ""
        if label in {"HR", "UTC"}:
            hours = _row_ints(line)
            hour_spans = _row_value_spans(line)
        elif label == "TMP":
            temps = _row_ints_at_spans(line, hour_spans) if hour_spans else _row_ints(line)
    if not hours or not temps:
        return []
    n = min(len(hours), len(temps))
    rows: list[dict] = []
    cur_day = run_time.date()
    prev_hour = run_time.hour
    for hour, temp_f in zip(hours[:n], temps[:n]):
        if hour < prev_hour:
            cur_day += timedelta(days=1)
        prev_hour = hour
        valid_time = datetime.combine(cur_day, time(hour, 0), tzinfo=timezone.utc)
        rows.append({
            "station": station.code,
            "source": source,
            "run_time": run_time,
            "valid_time": valid_time,
            "valid_date": _valid_date_for_tz(station.tz, valid_time),
            "lead_hr": _lead_hr(run_time, valid_time),
            "var": "TMP_2M",
            "value": float(temp_f),
            "units": "degF",
            "raw": {"parser": "HR/TMP", "station_id": station.code[1:]},
        })
    return rows


def fetch_obs_tracker(station: Station, now: datetime | None = None) -> list[dict]:
    """Store high-so-far for today from the existing METAR/HFMETAR table."""
    now = now or datetime.now(tz=timezone.utc)
    local_tz = pytz.timezone(station.tz)
    today = now.astimezone(local_tz).date()
    start = local_tz.localize(datetime.combine(today, time.min)).astimezone(timezone.utc)
    sql = """
    SELECT MAX(temp_f) AS tmax_so_far, COUNT(temp_f) AS n_obs, MAX(obs_time) AS latest_obs
      FROM metar_obs
     WHERE station = %s
       AND obs_time >= %s
       AND obs_time <= %s
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station.code, start, now))
        row = cur.fetchone()
    if not row or row["tmax_so_far"] is None:
        return []
    return [{
        "station": station.code,
        "source": "OBS_TRACKER",
        "run_time": now.replace(second=0, microsecond=0),
        "valid_time": now.replace(second=0, microsecond=0),
        "valid_date": today,
        "lead_hr": 0,
        "var": "OBS_TMAX_SO_FAR",
        "value": float(row["tmax_so_far"]),
        "units": "degF",
        "raw": {
            "n_obs": int(row["n_obs"] or 0),
            "latest_obs": row["latest_obs"].isoformat() if row.get("latest_obs") else None,
        },
    }]


def run(
    stations: Iterable[str] | None = None,
    sources: Iterable[str] | None = None,
    include_neighbors: bool = True,
) -> int:
    lookup = station_lookup(include_neighbors=include_neighbors)
    stations = list(stations or default_station_codes(include_neighbors=include_neighbors))
    source_set = {s.strip().upper() for s in (sources or ["NWS_GRID", "NWS_PFM", "LAMP", "MAV", "OBS_TRACKER"])}
    all_rows: list[dict] = []
    persistence.bootstrap_stations()

    shared_text: dict[str, ProductText | None] = {}
    for source in ("LAMP", "MAV"):
        if source in source_set:
            shared_text[source] = fetch_text_product(source)
            if shared_text[source] is None:
                log.warning("%s text product not available from configured public mirrors", source)

    for code in stations:
        station = lookup[code]
        for source in source_set:
            try:
                if source == "NWS_GRID":
                    rows = fetch_nws_grid(station)
                elif source == "NWS_PFM":
                    rows = fetch_pfm(station)
                elif source in {"LAMP", "MAV"}:
                    product = shared_text.get(source)
                    rows = parse_hourly_temp_guidance(product.text, station, source, product.run_time) if product else []
                    if rows:
                        for row in rows:
                            row["raw"] = {**(row.get("raw") or {}), "url": product.url}
                elif source == "OBS_TRACKER":
                    rows = fetch_obs_tracker(station)
                else:
                    log.warning("Unknown official guidance source %s", source)
                    rows = []
            except Exception as exc:
                log.warning("%s %s failed: %s", source, code, exc)
                rows = []
            if rows:
                log.info("%s %s: %d rows", source, code, len(rows))
                all_rows.extend(rows)

    persistence.upsert_forecast_guidance(all_rows)
    return len(all_rows)


def backfill_recent(
    days: int,
    stations: Iterable[str] | None = None,
    sources: Iterable[str] | None = None,
    include_neighbors: bool = True,
) -> int:
    """Backfill recent guidance available from public rolling archives.

    Implemented for PFM/LAMP/MAV. NWS_GRID is current/forward-only through
    api.weather.gov and OBS_TRACKER needs point-in-time reconstruction from
    METAR, so those are intentionally skipped here.
    """
    lookup = station_lookup(include_neighbors=include_neighbors)
    station_codes = list(stations or default_station_codes(include_neighbors=include_neighbors))
    source_set = {s.strip().upper() for s in (sources or ["NWS_PFM", "LAMP", "MAV"])}
    source_set &= {"NWS_PFM", "LAMP", "MAV"}
    if not source_set:
        return 0

    persistence.bootstrap_stations()
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=max(1, days))
    all_rows: list[dict] = []

    text_products: dict[tuple[str, datetime], ProductText] = {}
    if "LAMP" in source_set:
        for cycle in _lamp_history_cycles(start, end):
            product = fetch_text_product("LAMP", run_time=cycle)
            if product is not None:
                text_products[("LAMP", cycle)] = product
    if "MAV" in source_set:
        for cycle in _mav_history_cycles(start, end):
            product = fetch_text_product("MAV", run_time=cycle)
            if product is not None:
                text_products[("MAV", cycle)] = product

    for code in station_codes:
        station = lookup[code]
        if "NWS_PFM" in source_set:
            try:
                rows = fetch_pfm_history(station, start, end)
            except Exception as exc:
                log.warning("NWS_PFM backfill %s failed: %s", code, exc)
                rows = []
            if rows:
                log.info("NWS_PFM backfill %s: %d rows", code, len(rows))
                all_rows.extend(rows)
        for source in ("LAMP", "MAV"):
            if source not in source_set:
                continue
            count_before = len(all_rows)
            for product in (p for (src, _), p in text_products.items() if src == source):
                rows = parse_hourly_temp_guidance(product.text, station, source, product.run_time)
                for row in rows:
                    row["raw"] = {**(row.get("raw") or {}), "url": product.url}
                all_rows.extend(rows)
            count = len(all_rows) - count_before
            if count:
                log.info("%s backfill %s: %d rows", source, code, count)

    persistence.upsert_forecast_guidance(all_rows)
    return len(all_rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.info("official_guidance: %d rows persisted", run())
