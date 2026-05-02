"""NWS text-product client + parser for CLI (Daily Climate Report) and DSM
(Daily Summary Message).

CLI is the settlement authority for Kalshi NHIGH per the rule sheet — it's
forecaster-reviewed and published ~6-7 AM ET. DSM is the automated-ASOS
counterpart published shortly after midnight LST; we use it as an early
preview and cross-check.

Both products are accessible at:
    https://api.weather.gov/products?type=<CLI|DSM>&location=<XXX>

Where <XXX> is the 3-letter location code (NYC, ORD, MIA, ...). The API
returns a list of recent products; each has a UUID we then fetch by id.

This is a research-layer module — no DB writes, no caching beyond a simple
on-disk text dump for inspection.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

NWS_API = "https://api.weather.gov"
USER_AGENT = "weather_bot-research/0.1 (https://github.com/wallyworley/weatherbot)"

# Map ICAO → 3-letter NWS product location code. CLI/DSM both use this code
# in the API location filter.
STATION_TO_LOC = {
    "KNYC": "NYC", "KLGA": "LGA", "KORD": "ORD", "KMIA": "MIA",
    "KLAX": "LAX", "KDEN": "DEN", "KATL": "ATL", "KAUS": "AUS", "KPHL": "PHL",
}


@dataclass
class TextProduct:
    product_id: str
    issued: datetime
    location: str
    type_: str
    text: str


def _headers() -> dict:
    return {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}


def list_products(type_: str, location: str, start: Optional[datetime] = None,
                   end: Optional[datetime] = None, limit: int = 50) -> list[dict]:
    """List recent CLI/DSM products. Optional time window via ISO start/end."""
    params: dict = {"type": type_, "location": location, "limit": limit}
    if start is not None:
        params["start"] = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    if end is not None:
        params["end"] = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(f"{NWS_API}/products", params=params, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json().get("@graph", [])


def get_product(product_id: str) -> TextProduct:
    r = requests.get(f"{NWS_API}/products/{product_id}", headers=_headers(), timeout=30)
    r.raise_for_status()
    j = r.json()
    return TextProduct(
        product_id=j["id"],
        issued=datetime.fromisoformat(j["issuanceTime"].replace("Z", "+00:00")),
        location=j.get("location", ""),
        type_=j.get("productCode", ""),
        text=j.get("productText", ""),
    )


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------
# The "YESTERDAY" block of a CLI is a fixed-column climatological table:
#
#   WEATHER ITEM   OBSERVED TIME    RECORD YEAR NORMAL DEPARTURE LAST
#                   VALUE   (LST)    VALUE       VALUE  FROM      YEAR
#                                                       NORMAL
#   ...................................................................
#   TEMPERATURE (F)
#    YESTERDAY
#     MAXIMUM         72    400 PM    93   1944    66      6      80
#     MINIMUM         55   1235 AM    34   1874    53      2      59
#
# The first numeric token after MAXIMUM/MINIMUM is the observed value; the
# next is the time of observation (LST).

# Time format varies by WFO: "400 PM" (KNYC), "12:15 AM" (KORD). Accept both.
_CLI_MAX = re.compile(r"^\s*MAXIMUM\s+(-?\d+)\s+(\d{1,2}:?\d{2}\s*(?:AM|PM))",
                       re.MULTILINE | re.IGNORECASE)
_CLI_MIN = re.compile(r"^\s*MINIMUM\s+(-?\d+)\s+(\d{1,2}:?\d{2}\s*(?:AM|PM))",
                       re.MULTILINE | re.IGNORECASE)


@dataclass
class CliObservation:
    tmax_f: Optional[float]
    tmax_time_lst: Optional[str]
    tmin_f: Optional[float]
    tmin_time_lst: Optional[str]
    section: Optional[str] = None    # 'YESTERDAY' (final) | 'TODAY' (intraday) | None


def parse_cli_yesterday(text: str) -> CliObservation:
    """Extract daily TMAX/TMIN from a CLI body.

    CLIs come in two flavors:
      - Morning issue (typ. 04-14 UTC): final report for *yesterday's* climate day
      - Afternoon issue (typ. 18-23 UTC): intraday update covering *today* through 4 PM LST

    Settlement uses the morning final report. We pick the YESTERDAY section if
    it exists; otherwise fall back to TODAY (intraday). The `section` field on
    the returned obs flags which one was used so callers can filter.
    """
    # Try YESTERDAY first (the canonical settlement section).
    out = CliObservation(None, None, None, None, None)
    for label in ("YESTERDAY", "TODAY"):
        block_match = re.search(
            rf"\b{label}\b(.*?)(?:\bTODAY\b|\bYESTERDAY\b|\bMONTH TO DATE\b|\bSINCE\b|\Z)",
            text, re.DOTALL | re.IGNORECASE,
        )
        if not block_match:
            continue
        block = block_match.group(1)
        m_max = _CLI_MAX.search(block)
        m_min = _CLI_MIN.search(block)
        if m_max or m_min:
            out.section = label
            if m_max:
                out.tmax_f = float(m_max.group(1))
                out.tmax_time_lst = m_max.group(2).strip()
            if m_min:
                out.tmin_f = float(m_min.group(1))
                out.tmin_time_lst = m_min.group(2).strip()
            break
    return out


# ---------------------------------------------------------------------------
# DSM parser
# ---------------------------------------------------------------------------
# DSM uses a coded teletype format (FMH-1):
#
#   KNYC DS HHMM DD/MM TtttHHMM/ TtttHHMM// TTTT/ TTTT// hourly_precip ...
#                      ^^^^^^^^   ^^^^^^^^   ^^^^   ^^^^
#                      climate-day max+time, min+time, then 24-hr cal-day max, min
#
# Temperature is variable-width (1-3 digits, optional minus); time is always
# 4 digits (HHMM in LST). Climate-day is the official period for daily extremes.

_DSM_FIELDS = re.compile(
    r"\bDS\s+(?:\d{4}\s+)?\d{2}/\d{2}\s+"   # HHMM time prefix is omitted on morning DSMs
    r"(?P<tmax>-?\d{1,3})(?P<tmax_t>\d{4})/+\s*"
    r"(?P<tmin>-?\d{1,3})(?P<tmin_t>\d{4})/+",
    re.IGNORECASE,
)


@dataclass
class DsmObservation:
    tmax_f: Optional[float]
    tmax_time_lst: Optional[str]
    tmin_f: Optional[float]
    tmin_time_lst: Optional[str]


def parse_dsm(text: str) -> DsmObservation:
    out = DsmObservation(None, None, None, None)
    if m := _DSM_FIELDS.search(text):
        out.tmax_f = float(m.group("tmax"))
        out.tmax_time_lst = m.group("tmax_t")
        out.tmin_f = float(m.group("tmin"))
        out.tmin_time_lst = m.group("tmin_t")
    return out


# ---------------------------------------------------------------------------
# High-level helpers
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# IEM fallback for historical text products (NWS API only exposes ~6 days)
# ---------------------------------------------------------------------------
# IEM (Iowa Environmental Mesonet) archives every NWS text product back to
# the early 2000s. Their retrieve.py concatenates products in a date range;
# we ask for one issue-day at a time, ascending, so the morning issuance
# (with the YESTERDAY section) lands first.
IEM_RETRIEVE = "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"


def fetch_text_iem(type_: str, station: str, target_date) -> Optional[str]:
    """Pull raw text for the CLI/DSM that reports `target_date`'s data via IEM.

    Returns concatenated raw text (multi-product blob is fine — `parse_cli_yesterday`
    will lock onto the first YESTERDAY section, which is the morning issuance).
    """
    from datetime import timedelta
    loc = STATION_TO_LOC.get(station)
    if not loc:
        return None
    pil = f"{type_}{loc}"
    issue = target_date + timedelta(days=1)
    next_d = target_date + timedelta(days=2)
    params = {
        "pil": pil, "fmt": "text",
        "sdate": issue.isoformat(), "edate": next_d.isoformat(),
        "order": "asc", "limit": "10",
    }
    try:
        r = requests.get(IEM_RETRIEVE, params=params,
                          headers={"User-Agent": USER_AGENT}, timeout=20)
    except Exception as e:
        log.warning("IEM fetch %s %s: %s", pil, target_date, e)
        return None
    if r.status_code != 200 or len(r.content) < 100:
        return None
    return r.text


def latest_cli_for_station(station: str) -> Optional[tuple[TextProduct, CliObservation]]:
    loc = STATION_TO_LOC.get(station)
    if not loc:
        return None
    products = list_products("CLI", loc, limit=5)
    if not products:
        return None
    prod = get_product(products[0]["id"])
    return prod, parse_cli_yesterday(prod.text)


def latest_dsm_for_station(station: str) -> Optional[tuple[TextProduct, DsmObservation]]:
    loc = STATION_TO_LOC.get(station)
    if not loc:
        return None
    products = list_products("DSM", loc, limit=5)
    if not products:
        return None
    prod = get_product(products[0]["id"])
    return prod, parse_dsm(prod.text)


def cli_history(station: str, start: datetime, end: datetime) -> list[tuple[TextProduct, CliObservation]]:
    loc = STATION_TO_LOC[station]
    products = list_products("CLI", loc, start=start, end=end, limit=200)
    out = []
    for p in products:
        prod = get_product(p["id"])
        out.append((prod, parse_cli_yesterday(prod.text)))
    return out


def save_raw(prod: TextProduct, dest_dir: Path) -> Path:
    """Dump raw product text to disk for manual inspection / parser debugging."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{prod.type_}_{prod.location}_{prod.issued:%Y%m%d_%H%M}.txt"
    path = dest_dir / fname
    path.write_text(prod.text)
    return path


if __name__ == "__main__":
    import argparse, json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--station", default="KNYC")
    ap.add_argument("--type", choices=["CLI", "DSM", "BOTH"], default="BOTH")
    ap.add_argument("--save-to", default="research/reports/raw")
    args = ap.parse_args()

    out: dict = {"station": args.station}
    if args.type in ("CLI", "BOTH"):
        r = latest_cli_for_station(args.station)
        if r:
            prod, obs = r
            save_raw(prod, Path(args.save_to))
            out["cli"] = {"issued": prod.issued.isoformat(), "tmax_f": obs.tmax_f,
                           "tmax_time_lst": obs.tmax_time_lst, "tmin_f": obs.tmin_f,
                           "tmin_time_lst": obs.tmin_time_lst}
    if args.type in ("DSM", "BOTH"):
        r = latest_dsm_for_station(args.station)
        if r:
            prod, obs = r
            save_raw(prod, Path(args.save_to))
            out["dsm"] = {"issued": prod.issued.isoformat(), "tmax_f": obs.tmax_f,
                           "tmax_time_lst": obs.tmax_time_lst, "tmin_f": obs.tmin_f,
                           "tmin_time_lst": obs.tmin_time_lst}
    print(json.dumps(out, indent=2, default=str))
