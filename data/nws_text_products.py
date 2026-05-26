"""Production-grade NWS CLI / DSM fetcher + persistence.

Promoted from research/sources/nws_text_products.py after the 30-day comparison
(see research/reports/obs_compare_*.md) showed METAR-derived daily TMAX
understates the official NWS Daily Climate Report (CLI) by 0.5-1°F across all
3 fetch stations. CLI is also the explicit Kalshi NHIGH settlement source per
the Kalshi rule sheet.

Strategy:
- CLI is the settlement authority. Fetch the morning issuance (06-14 UTC) of
  (target_date + 1) — that issuance reports target_date's YESTERDAY data.
- DSM is an early preview where issued (NYC only in our station set; not ORD/MIA).
  Useful for spotting CLI-vs-DSM disagreement that triggers Kalshi's 11 AM-delay
  rule, but not used for primary settlement.
- NWS API exposes only ~6 days of archive; older dates fall back to IEM
  (mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from weather_bot.data import persistence

log = logging.getLogger(__name__)

NWS_API = "https://api.weather.gov"
USER_AGENT = "weather_bot/0.1 (https://github.com/wallyworley/weatherbot)"
IEM_RETRIEVE = "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"

# ICAO → 3-letter NWS product location code used in CLI/DSM PILs.
# Most stations use their airport's IATA code as the PIL (e.g. KBOS → BOS,
# CLIBOS). Verified 2026-05-26 by attempting CLI fetches against the live
# NWS API for each entry.
STATION_TO_LOC = {
    "KNYC": "NYC", "KLGA": "LGA",
    "KMDW": "MDW",   # Chicago Midway — Kalshi's CHI settlement station
    "KORD": "ORD",   # Chicago O'Hare — kept for backward compatibility, not used by trading
    "KMIA": "MIA",
    "KLAX": "LAX", "KDEN": "DEN", "KATL": "ATL", "KAUS": "AUS", "KPHL": "PHL",
    # 2026-05-26 expansion. PILs verified by hitting
    # api.weather.gov/products/types/CLI/locations/<PIL> for each candidate.
    # Every one matches the airport's IATA code; the WFO-3-letter alternatives
    # (VEF, LIX, EWX) returned 0 products and were wrong guesses.
    "KDCA": "DCA",
    "KBOS": "BOS",
    "KPHX": "PHX",
    "KDFW": "DFW",
    "KSFO": "SFO",
    "KSEA": "SEA",
    "KLAS": "LAS",
    "KMSY": "MSY",
    "KMSP": "MSP",
    "KSAT": "SAT",
    "KOKC": "OKC",
}


@dataclass
class CliObservation:
    tmax_f: Optional[float]
    tmax_time_lst: Optional[str]
    tmin_f: Optional[float]
    tmin_time_lst: Optional[str]
    section: Optional[str] = None    # 'YESTERDAY' (final) | 'TODAY' (intraday) | None


@dataclass
class DsmObservation:
    tmax_f: Optional[float]
    tmax_time_lst: Optional[str]
    tmin_f: Optional[float]
    tmin_time_lst: Optional[str]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
# Time format varies by WFO: "400 PM" (KNYC), "12:15 AM" (KORD). Accept both.
# Temperature value may be followed by a flag letter:
#   R = tied or broke a record (KMIA 2026-05-02 had "94R")
#   E = estimated, M = missing, P = preliminary (per NWS CLI format docs)
# `[A-Z]*` consumes any/all flag letters before the time field.
_CLI_MAX = re.compile(r"^\s*MAXIMUM\s+(-?\d+)[A-Z]*\s+(\d{1,2}:?\d{2}\s*(?:AM|PM))",
                       re.MULTILINE | re.IGNORECASE)
_CLI_MIN = re.compile(r"^\s*MINIMUM\s+(-?\d+)[A-Z]*\s+(\d{1,2}:?\d{2}\s*(?:AM|PM))",
                       re.MULTILINE | re.IGNORECASE)


def parse_cli(text: str) -> CliObservation:
    """Extract daily TMAX/TMIN from a CLI body. Tries YESTERDAY first (the
    canonical settlement section); falls back to TODAY (intraday)."""
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


# DSM coded format: KNYC DS [HHMM ]DD/MM TtttHHMM/ TtttHHMM// ...
_DSM_FIELDS = re.compile(
    r"\bDS\s+(?:\d{4}\s+)?\d{2}/\d{2}\s+"
    r"(?P<tmax>-?\d{1,3})(?P<tmax_t>\d{4})/+\s*"
    r"(?P<tmin>-?\d{1,3})(?P<tmin_t>\d{4})/+",
    re.IGNORECASE,
)


def parse_dsm(text: str) -> DsmObservation:
    out = DsmObservation(None, None, None, None)
    if m := _DSM_FIELDS.search(text):
        out.tmax_f = float(m.group("tmax"))
        out.tmax_time_lst = m.group("tmax_t")
        out.tmin_f = float(m.group("tmin"))
        out.tmin_time_lst = m.group("tmin_t")
    return out


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------
def _nws_morning_window(target_date) -> tuple[datetime, datetime]:
    """target_date+1 00:00–18:00 UTC — captures the morning final-yesterday CLI/DSM
    while excluding afternoon intraday issuances (typically 20+ UTC)."""
    start = datetime.combine(target_date + timedelta(days=1),
                              datetime.min.time(), tzinfo=timezone.utc)
    return start, start + timedelta(hours=18)


def _nws_list(type_: str, location: str, start: datetime, end: datetime) -> list[dict]:
    params = {
        "type": type_, "location": location, "limit": 20,
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end":   end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    r = requests.get(f"{NWS_API}/products", params=params,
                      headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
                      timeout=20)
    r.raise_for_status()
    return r.json().get("@graph", [])


def _nws_get(product_id: str) -> tuple[str, datetime]:
    r = requests.get(f"{NWS_API}/products/{product_id}",
                      headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json"},
                      timeout=20)
    r.raise_for_status()
    j = r.json()
    issued = datetime.fromisoformat(j["issuanceTime"].replace("Z", "+00:00"))
    return j.get("productText", ""), issued


def _iem_retrieve(pil: str, target_date) -> Optional[tuple[str, datetime]]:
    """IEM fallback (older than NWS API archive). Issuance time approximated
    from the AFOS header via the request window start since IEM doesn't expose
    structured issuance times via this endpoint."""
    issue = target_date + timedelta(days=1)
    next_d = target_date + timedelta(days=2)
    params = {"pil": pil, "fmt": "text",
               "sdate": issue.isoformat(), "edate": next_d.isoformat(),
               "order": "asc", "limit": "10"}
    try:
        r = requests.get(IEM_RETRIEVE, params=params,
                          headers={"User-Agent": USER_AGENT}, timeout=20)
    except Exception as e:
        log.warning("IEM fetch %s %s: %s", pil, target_date, e)
        return None
    if r.status_code != 200 or len(r.content) < 100:
        return None
    # Approximate issuance: midnight UTC of issue date (no header parsing).
    return r.text, datetime.combine(issue, datetime.min.time(), tzinfo=timezone.utc)


def fetch_cli(station: str, target_date) -> Optional[tuple[CliObservation, str, datetime]]:
    """Fetch and parse the CLI for `target_date`'s data. Returns (parsed, raw_text, issued_at)."""
    loc = STATION_TO_LOC.get(station)
    if not loc:
        return None
    start, end = _nws_morning_window(target_date)
    if (datetime.now(tz=timezone.utc).date() - target_date).days <= 6:
        try:
            products = _nws_list("CLI", loc, start, end)
            if products:
                text, issued = _nws_get(products[0]["id"])
                return parse_cli(text), text, issued
        except Exception as e:
            log.warning("NWS CLI %s %s failed: %s", station, target_date, e)
    iem = _iem_retrieve(f"CLI{loc}", target_date)
    if iem:
        text, issued = iem
        return parse_cli(text), text, issued
    return None


def fetch_dsm(station: str, target_date) -> Optional[tuple[DsmObservation, str, datetime]]:
    loc = STATION_TO_LOC.get(station)
    if not loc:
        return None
    start, end = _nws_morning_window(target_date)
    if (datetime.now(tz=timezone.utc).date() - target_date).days <= 6:
        try:
            products = _nws_list("DSM", loc, start, end)
            if products:
                text, issued = _nws_get(products[0]["id"])
                return parse_dsm(text), text, issued
        except Exception as e:
            log.warning("NWS DSM %s %s failed: %s", station, target_date, e)
    iem = _iem_retrieve(f"DSM{loc}", target_date)
    if iem:
        text, issued = iem
        return parse_dsm(text), text, issued
    return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def upsert_cli_obs(station: str, local_date, obs: CliObservation, raw_text: str,
                    issued_at: datetime) -> None:
    sql = """
    INSERT INTO cli_obs(station, local_date, tmax_f, tmax_time_lst, tmin_f, tmin_time_lst,
                         section, issued_at, raw_text)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (station, local_date) DO UPDATE SET
      tmax_f = EXCLUDED.tmax_f,
      tmax_time_lst = EXCLUDED.tmax_time_lst,
      tmin_f = EXCLUDED.tmin_f,
      tmin_time_lst = EXCLUDED.tmin_time_lst,
      section = EXCLUDED.section,
      issued_at = EXCLUDED.issued_at,
      raw_text = EXCLUDED.raw_text,
      fetched_at = now()
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, local_date, obs.tmax_f, obs.tmax_time_lst,
                           obs.tmin_f, obs.tmin_time_lst, obs.section,
                           issued_at, raw_text))
        conn.commit()


def get_cli_tmax(station: str, local_date) -> Optional[float]:
    """Authoritative TMAX from CLI for settlement. Returns None if not yet captured."""
    sql = "SELECT tmax_f FROM cli_obs WHERE station=%s AND local_date=%s"
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, local_date))
        r = cur.fetchone()
    return float(r["tmax_f"]) if r and r["tmax_f"] is not None else None


def get_cli_tmin(station: str, local_date) -> Optional[float]:
    sql = "SELECT tmin_f FROM cli_obs WHERE station=%s AND local_date=%s"
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, local_date))
        r = cur.fetchone()
    return float(r["tmin_f"]) if r and r["tmin_f"] is not None else None
