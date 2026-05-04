"""
Global configuration. Keep this small and boring.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_CACHE = PROJECT_ROOT / ".cache"
DATA_CACHE.mkdir(exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://weather:weather@localhost:5432/weather_bot")

# ---------------------------------------------------------------------------
# Stations
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Station:
    code: str           # ICAO / Kalshi station id
    name: str
    lat: float
    lon: float          # negative west
    tz: str
    # ASOS stations have a 5-min MADIS HFMETAR feed available via IEM that
    # closes the ~0.8°F undercount of hourly METAR vs CLI (verified 2026-05-03,
    # research/compare_hfmetar.py). KNYC is a coop site, not ASOS — no feed,
    # leave on hourly. See memory/knyc_no_hfmetar.md.
    is_asos: bool = True

# Kalshi daily temp contracts reference these NWS stations.
STATIONS: dict[str, Station] = {
    "KNYC": Station("KNYC", "New York Central Park", 40.7794, -73.9692, "America/New_York", is_asos=False),
    "KLGA": Station("KLGA", "New York LaGuardia",    40.7772, -73.8726, "America/New_York"),
    # Kalshi NHIGH for Chicago resolves on **Midway (KMDW)** per the rule sheet,
    # not O'Hare (KORD). Verified 2026-05-02 by inspecting market payloads
    # ("highest temperature recorded at Chicago Midway, IL...").
    "KMDW": Station("KMDW", "Chicago Midway",        41.7868, -87.7522, "America/Chicago"),
    "KORD": Station("KORD", "Chicago O'Hare",        41.9786, -87.9048, "America/Chicago"),
    "KLAX": Station("KLAX", "Los Angeles Intl",      33.9381, -118.3889, "America/Los_Angeles"),
    "KMIA": Station("KMIA", "Miami Intl",            25.7933, -80.2906, "America/New_York"),
    "KDEN": Station("KDEN", "Denver Intl",           39.8466, -104.6564, "America/Denver"),
    "KATL": Station("KATL", "Atlanta Hartsfield",    33.6367, -84.4281, "America/New_York"),
    "KAUS": Station("KAUS", "Austin-Bergstrom",      30.1950, -97.6700, "America/Chicago"),
    "KPHL": Station("KPHL", "Philadelphia Intl",     39.8744, -75.2424, "America/New_York"),
}

# Neighbor stations for spatial-gradient triangulation around each primary
# station. METAR is pulled for these alongside the primaries; daily TMAX is
# NOT computed (they don't drive settlement) — they're purely diagnostic
# inputs for "which way is the regional temperature field moving?".
# Neighbors picked to span coastal/inland and N/S/E/W of the primary so the
# spread is informative. Inspired by dailydewpoint.com's NYC panel.
NEIGHBOR_STATIONS: dict[str, list[Station]] = {
    "KNYC": [
        Station("KJFK", "JFK Intl",       40.6398, -73.7789, "America/New_York"),
        Station("KLGA", "LaGuardia",      40.7772, -73.8726, "America/New_York"),
        Station("KEWR", "Newark Intl",    40.6925, -74.1687, "America/New_York"),
        Station("KTEB", "Teterboro",      40.8501, -74.0608, "America/New_York"),
        Station("KCDW", "Caldwell NJ",    40.8753, -74.2814, "America/New_York"),
        Station("KSMQ", "Somerset NJ",    40.6260, -74.6700, "America/New_York"),
    ],
    "KMDW": [
        Station("KORD", "Chicago O'Hare", 41.9786, -87.9048, "America/Chicago"),
        Station("KDPA", "DuPage",         41.9078, -88.2486, "America/Chicago"),
        Station("KPWK", "Palwaukee",      42.1142, -87.9015, "America/Chicago"),
        Station("KGYY", "Gary IN",        41.6163, -87.4128, "America/Chicago"),
    ],
    "KMIA": [
        Station("KFLL", "Ft Lauderdale",  26.0742, -80.1506, "America/New_York"),
        Station("KOPF", "Opa-Locka",      25.9072, -80.2782, "America/New_York"),
        Station("KHWO", "Hollywood",      26.0014, -80.2407, "America/New_York"),
        Station("KTMB", "Kendall-Tamiami",25.6479, -80.4327, "America/New_York"),
    ],
}


# Two-list split: fetchers ingest data + bias is computed for FETCH stations,
# but only TRADE stations actually have markets scored and paper-filled.
# A station graduates from fetch-only to trade-eligible once its bias table
# has sample_size >= 10 for the current month at lead_day in {0,1,2}.
ACTIVE_FETCH_STATIONS: list[str] = ["KNYC", "KMDW", "KMIA"]
# 2026-05-02: graduated KMDW + KMIA to active trading. All stations pass
# bias gate at lead 0/1/2 per is_station_calibrated check. The pre-trade
# BIAS_GATE remains the safety net.
# 2026-05-02 LATER: discovered Kalshi's CHI markets resolve on KMDW (Midway),
# not KORD (O'Hare). Switched Chicago station from KORD to KMDW. Bias tables
# for KMDW will be empty initially → BIAS_GATE will block KMDW trades for
# ~2-4 weeks until enough samples accumulate. Expected behavior.
ACTIVE_TRADE_STATIONS: list[str] = ["KNYC", "KMDW", "KMIA"]
# Backwards-compat alias — all existing fetcher / retrain code uses this name.
ACTIVE_STATIONS: list[str] = ACTIVE_FETCH_STATIONS

# ---------------------------------------------------------------------------
# NOAA data sources (NOAA Big Data Program — free, fast, public S3)
# ---------------------------------------------------------------------------
NBM_BUCKET = "noaa-nbm-grib2-pds"
HRRR_BUCKET = "noaa-hrrr-bdp-pds"

# NBM probabilistic percentiles: blend.tCCz.qmd.fNNN.co.grib2
# NBM deterministic core:        blend.tCCz.core.fNNN.co.grib2
NBM_PREFIX = "blend.{yyyymmdd}/{cc:02d}/{product}"

HRRR_PREFIX = "hrrr.{yyyymmdd}/conus"

# GRIB2 variable selectors (used to grep .idx files).
# See https://www.nco.ncep.noaa.gov/pmb/products/blend/ for full inventory.
NBM_PROB_SELECTORS = [
    # Percentiles we care about for temperature.
    ":TMP:2 m above ground:",      # deterministic mean inside QMD file (fallback)
]
NBM_PERCENTILES = [1, 5, 10, 25, 50, 75, 90, 95, 99]

HRRR_SELECTORS = [":TMP:2 m above ground:"]

# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------
AVIATION_WEATHER_METAR_URL = (
    "https://aviationweather.gov/api/data/metar?ids={station}&format=json&hours={hours}"
)
# For historical pulls: anchor the lookback window at a specific UTC timestamp.
AVIATION_WEATHER_METAR_URL_DATED = (
    "https://aviationweather.gov/api/data/metar?ids={station}&format=json&hours={hours}&date={date}"
)
# API reliably returns ~72h per call. We chunk historical pulls in this window.
METAR_BACKFILL_CHUNK_HOURS = 72

# METAR sanity filter — drops physically implausible temperature swings
# (e.g., NWS station API occasionally returns stale afternoon-warm readings
# mixed into overnight sequences, which would corrupt daily TMAX and pollute
# bias correction). Conservative defaults — real weather can swing 6-8°F in
# 30 min during convective passage, so we only catch the extremes.
METAR_GUARD_MAX_DELTA_F = float(os.getenv("METAR_GUARD_MAX_DELTA_F", "10.0"))
METAR_GUARD_WINDOW_MIN  = int(os.getenv("METAR_GUARD_WINDOW_MIN", "30"))

# Iowa Environmental Mesonet (IEM) ASOS archive — canonical historical METAR
# source. aviationweather.gov's dated endpoint is unreliable beyond ~48h, so we
# use IEM for backfill and aviationweather.gov for live pulls.
IEM_ASOS_URL = (
    "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
    "station={station}&data=tmpf,dwpf,sknt,metar"
    "&year1={y1}&month1={m1}&day1={d1}&hour1=0&minute1=0"
    "&year2={y2}&month2={m2}&day2={d2}&hour2=0&minute2=0"
    "&tz=Etc%2FUTC&format=onlycomma&latlon=no&missing=null&trace=null"
    "&direct=yes&report_type=3&report_type=4"
)

# IEM 5-minute MADIS HFMETAR feed. Includes routine METAR (report_type=3,4),
# SPECI, and the 5-min HFMETAR rows (report_type=1) — the latter have CSV
# tmpf=M but carry temp/dewpoint to 0.1°C in the raw METAR's Txxxxxxxx group.
# Use for live morning-window monitoring; IEM throttles 1 req/sec/IP.
IEM_ASOS_RECENT_URL = (
    "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
    "station={station}&data=tmpf,dwpf,sknt,metar"
    "&hours={hours}"
    "&tz=Etc%2FUTC&format=onlycomma&latlon=no&missing=null&trace=null"
    "&direct=yes"
)

# IEM date-ranged URL with no report_type filter — returns routine METAR +
# SPECI + 5-min MADIS HFMETAR. Used for historical 5-min backtests.
IEM_ASOS_HFMETAR_URL = (
    "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
    "station={station}&data=tmpf,dwpf,sknt,metar"
    "&year1={y1}&month1={m1}&day1={d1}&hour1=0&minute1=0"
    "&year2={y2}&month2={m2}&day2={d2}&hour2=0&minute2=0"
    "&tz=Etc%2FUTC&format=onlycomma&latlon=no&missing=null&trace=null"
    "&direct=yes"
)

# ---------------------------------------------------------------------------
# Kalshi
# ---------------------------------------------------------------------------
KALSHI_BASE_URL = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2")
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"

# Kalshi fee model (as of 2026-04). Formula: round_up(0.07 * C * P * (1 - P))
KALSHI_FEE_COEFF = 0.07

# ---------------------------------------------------------------------------
# Multi-model directional agreement gate
# ---------------------------------------------------------------------------
# Each candidate signal computes a directional vote per model (NBM p50, HRRR
# daily TMAX, GFS daily TMAX). Vote = "is the model's point estimate in the
# bucket?" If REQUIRE_AGREEMENT_N > 0 and fewer than N models agree with the
# bot's chosen side, the signal is rejected with skip_reason='AGREEMENT'.
# Default is 0 (disabled — votes are recorded for diagnostics only). When
# turning on, recommended starting value is 2 (of 3 models).
REQUIRE_AGREEMENT_N = int(os.getenv("REQUIRE_AGREEMENT_N", "0"))

# ---------------------------------------------------------------------------
# Risk / sizing
# ---------------------------------------------------------------------------
BANKROLL_USD = float(os.getenv("BANKROLL_USD", "1000"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.02"))
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))
MIN_EDGE_BPS = int(os.getenv("MIN_EDGE_BPS", "200"))   # 200 bps = 2 cents per $1
