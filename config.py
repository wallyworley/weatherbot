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
    # 2026-05-24 expansion: added 11 cities with live KXHIGH markets, fetch-only.
    # Station chosen to match the NWS CLI source named in each market's
    # rules_primary (verified via Kalshi API probe).
    "KDCA": Station("KDCA", "Washington Reagan Natl", 38.8512, -77.0402, "America/New_York"),
    "KBOS": Station("KBOS", "Boston Logan",           42.3656, -71.0096, "America/New_York"),
    "KPHX": Station("KPHX", "Phoenix Sky Harbor",     33.4373, -112.0078, "America/Phoenix"),
    "KDFW": Station("KDFW", "Dallas-Fort Worth",      32.8998, -97.0403, "America/Chicago"),
    "KSFO": Station("KSFO", "San Francisco Intl",     37.6213, -122.3790, "America/Los_Angeles"),
    "KSEA": Station("KSEA", "Seattle-Tacoma",         47.4502, -122.3088, "America/Los_Angeles"),
    "KLAS": Station("KLAS", "Las Vegas Harry Reid",   36.0840, -115.1537, "America/Los_Angeles"),
    "KMSY": Station("KMSY", "New Orleans Louis Armstrong", 29.9934, -90.2580, "America/Chicago"),
    "KMSP": Station("KMSP", "Minneapolis-St Paul",    44.8848, -93.2223, "America/Chicago"),
    "KSAT": Station("KSAT", "San Antonio Intl",       29.5337, -98.4698, "America/Chicago"),
    "KOKC": Station("KOKC", "Oklahoma City Will Rogers", 35.3931, -97.6007, "America/Chicago"),
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
ACTIVE_FETCH_STATIONS: list[str] = [
    # Originally fetched (KLGA/KORD are neighbor stations, not Kalshi targets):
    "KNYC", "KMDW", "KMIA", "KLGA", "KORD",
    # 2026-05-24 expansion: fetch-only KXHIGH cities for bias accumulation.
    # BIAS_GATE will block all trades until each cell has n>=10 — paper safe.
    "KLAX", "KATL", "KDCA", "KPHL", "KDEN", "KBOS", "KPHX", "KAUS",
    "KDFW", "KSFO", "KSEA", "KLAS", "KMSY", "KMSP", "KSAT", "KOKC",
]
# 2026-05-02: graduated KMDW + KMIA to active trading. All stations pass
# bias gate at lead 0/1/2 per is_station_calibrated check. The pre-trade
# BIAS_GATE remains the safety net.
# 2026-05-02 LATER: discovered Kalshi's CHI markets resolve on KMDW (Midway),
# not KORD (O'Hare). Switched Chicago station from KORD to KMDW. Bias tables
# for KMDW will be empty initially → BIAS_GATE will block KMDW trades for
# ~2-4 weeks until enough samples accumulate. Expected behavior.
# 2026-05-26: graduated all 16 fetch-only cities to active trading.
# Justification (research/threshold_audit.py + ad-hoc bias query):
#   - After CLI/METAR/NBM backfill, every new city has n=21 for May lead=0
#   - 13/16 have |bias| < 1.0°F and stddev < 3.0°F — substantially better
#     calibration than the existing trio (KMDW +4.43°F σ=5.95, KMIA +2.61°F
#     σ=3.64, KNYC similar to KMDW)
#   - LEAD_DAY_GATE blocks lead>=1 universally; NO_FADE_GATE blocks NO<$0.50;
#     BIAS_GATE re-checks n>=10 at trade time. Safety rails intact.
# Promotion at full Kelly per the existing cells. Re-audit per-city PnL on
# or after 2026-06-02 to see which actually have edge vs which need pausing.
ACTIVE_TRADE_STATIONS: list[str] = [
    "KNYC", "KMDW", "KMIA",          # original trio
    "KPHX", "KLAS",                   # desert / climatologically stable
    "KMSY", "KDCA", "KSFO", "KDFW",   # near-zero bias, moderate stddev
    "KATL", "KPHL", "KOKC", "KLAX",
    "KDEN", "KAUS", "KSAT",
    "KBOS", "KSEA", "KMSP",           # slightly higher bias / stddev
]
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
PAPER_ORDER_MODE = os.getenv("PAPER_ORDER_MODE", "true").lower() == "true"

# In paper mode the point of running is to generate settled fills that
# validate (or invalidate) the model. TRIPWIRE_RED and PAUSED_TRADE_STATIONS
# exist to protect *live* capital — applying them in paper starves the
# feedback loop. Default: bypass these two gates when PAPER_MODE=true.
# BIAS_GATE and DIVERGENCE remain on in paper because they prevent
# meaningless or upstream-broken signals from polluting the calibration set.
PAPER_BYPASS_TRIPWIRE = os.getenv("PAPER_BYPASS_TRIPWIRE", "true").lower() == "true"
PAPER_BYPASS_STATION_PAUSE = os.getenv("PAPER_BYPASS_STATION_PAUSE", "true").lower() == "true"

# Take-profit / early-exit threshold: fraction of max possible gain at which
# the bot closes an open position at the current bid. 2026-05-25 audit on
# 30d of fills showed lower thresholds capture more brief upticks before
# they fade back to zero (e.g., 0.50 would have improved net by ~$363 vs
# 0.85). Caveat: audit used peak-of-window prices, real bot exits at
# first-crossing, so realized gain is smaller. Set via env so we can A/B
# without a redeploy.
TAKE_PROFIT_THRESHOLD = float(os.getenv("TAKE_PROFIT_THRESHOLD", "0.85"))
PAPER_ORDER_IMPROVEMENT_CENTS = int(os.getenv("PAPER_ORDER_IMPROVEMENT_CENTS", "3"))
PAPER_ORDER_TTL_MIN = int(os.getenv("PAPER_ORDER_TTL_MIN", "15"))

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

# ---------------------------------------------------------------------------
# Probability calibration
# ---------------------------------------------------------------------------
# Raw CDF bucket probabilities are weather-model probabilities. Before sizing,
# shrink them toward what similarly-priced paper predictions have actually done.
# This is deliberately simple and auditable: decile bucket -> observed frequency,
# with a prior pulling small samples back toward the raw model.
PROB_CALIBRATION_ENABLED = os.getenv("PROB_CALIBRATION_ENABLED", "true").lower() == "true"
PROB_CALIBRATION_DAYS_BACK = int(os.getenv("PROB_CALIBRATION_DAYS_BACK", "60"))
PROB_CALIBRATION_MIN_BUCKET_N = int(os.getenv("PROB_CALIBRATION_MIN_BUCKET_N", "20"))
PROB_CALIBRATION_PRIOR_N = float(os.getenv("PROB_CALIBRATION_PRIOR_N", "35"))
PROB_CALIBRATION_MAX_DELTA = float(os.getenv("PROB_CALIBRATION_MAX_DELTA", "0.15"))

# ---------------------------------------------------------------------------
# Profitability controls (paper/live entry shaping)
# ---------------------------------------------------------------------------
# These are deliberately simple, evidence-backed controls from the corrected
# paper-fill slices. They reduce or block slices that have been persistent
# losers while keeping the core weather model intact.
PROFIT_CONTROLS_ENABLED = os.getenv("PROFIT_CONTROLS_ENABLED", "true").lower() == "true"
PAUSED_TRADE_STATIONS = [
    s.strip().upper()
    for s in os.getenv("PAUSED_TRADE_STATIONS", "KMDW").split(",")
    if s.strip()
]

# KNYC same-day has carried the historical edge; KNYC day-ahead has not.
KNYC_L1_SIZE_MULT = float(os.getenv("KNYC_L1_SIZE_MULT", "0.25"))

# Side/price-band controls from corrected-fee slices:
#   - NO below 50c has been poor: default is now block, not half-size.
#   - YES below 10c has been poor.
#   - YES 10-25c is the only low-price convexity sleeve with positive history;
#     keep it capped until it proves itself out of sample.
#   - YES 25-50c has been poor.
NO_UNDER_50C_SIZE_MULT = float(os.getenv("NO_UNDER_50C_SIZE_MULT", "0.0"))
YES_UNDER_10C_SIZE_MULT = float(os.getenv("YES_UNDER_10C_SIZE_MULT", "0.0"))
YES_10_25C_SIZE_MULT = float(os.getenv("YES_10_25C_SIZE_MULT", "0.50"))
YES_10_25C_MAX_USD = float(os.getenv("YES_10_25C_MAX_USD", "25.0"))
YES_25_50C_SIZE_MULT = float(os.getenv("YES_25_50C_SIZE_MULT", "0.50"))

# Paper/live execution quality controls. The main loop fetches a fresh book
# directly, then refuses to write a paper fill larger than top-of-book size.
REQUIRE_TOP_BOOK_SIZE = os.getenv("REQUIRE_TOP_BOOK_SIZE", "true").lower() == "true"
