"""EXP-2026-011 — Market Reaction Latency report. RESEARCH-ONLY.

Implements docs/research/EXP_2026_011_MARKET_REACTION_LATENCY_AUDIT.md (LOCKED).

For each pre-registered public-info channel, measures the ordering
    event official_ts  ->  WeatherBot first_seen_at  ->  Kalshi reprice onset
using GENUINE forward `first_seen_at` from `info_provenance` (no historical backfill).
The single per-channel statistic is the distribution of
    lag = (first reprice onset after official_ts) - first_seen_at
reported as median lag and positive-lag fraction, per station, counting unique
station/date event-days. Measurement only; this script never trades and promotes nothing.

Run on the VPS against the VPS local DB. Do not export rows to local.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ---- LOCKED parameters (prereg sections 6, 7) ----
MATERIAL_MOVE_F = 0.10          # |market-center move| that counts as a reprice
PRE_MIN = 30                    # minutes before official_ts to establish the baseline center
POST_MIN = 60                   # minutes after official_ts to look for the onset
CAND_MEDIAN_LAG_MIN = 2.0       # candidate: median lag >= 2 min
CAND_POS_FRAC = 0.60            # candidate: positive-lag fraction >= 60%
CAND_MIN_EVENT_DAYS = 100       # candidate: >= 100 unique station/date event-days
CAND_MIN_STATIONS = 2           # candidate: >= 2 stations

# Per-channel config (locked amendments A1, A3). `cap_min` = max plausible forward ingest latency
# on first_seen_at - official_ts (second guard against startup/stale backfill; the --since
# instrumentation-start cutoff is the primary guard). `anchor`:
#   "official"            -> anchor latency on official_ts (METAR obs time, CLI issue time);
#                            genuine event time, close to first_seen.
#   "first_seen_window"   -> Option A for model runs: official_ts (run cycle time) is metadata
#                            only; anchor on first_seen_at, baseline at (first_seen - pre), search
#                            the full window so a move BEFORE we saw the run is a negative lag
#                            (already priced) and a move after is positive (we saw it first).
CHANNELS = {
    "metar": {"sources": ("metar", "metar_lowlat"), "cap_min": 60, "anchor": "official"},
    "model_run": {"sources": ("nbm_run", "hrrr_run", "gfs_run", "ecmwf_run"), "cap_min": 480, "anchor": "first_seen_window"},
    "cli": {"sources": ("cli", "dsm"), "cap_min": 360, "anchor": "official"},
}

# Cross-venue same-station map (amendment A2). Expanded 2 -> 7 on 2026-06-09 after rules
# verification; citation record: docs/research/EXP_2026_011_CROSS_VENUE_MAP_VERIFICATION.md.
# Only these Kalshi stations are comparable to Polymarket; all others excluded until verified.
CROSSVENUE_COMPARABLE = ("KATL", "KMIA", "KAUS", "KSEA", "KLAX", "KHOU", "KSFO")
CROSSVENUE_EXCLUDED = ("KNYC", "KMDW", "KDEN", "KDFW")  # different physical stations

# ---- LOCKED cross-venue episode parameters (codex call 2026-06-09, f2e7031) ----
XV_GAP_F = 0.50            # fresh-episode divergence threshold |poly_center - kalshi_center|
XV_REARM_F = 0.25          # re-arm band: |gap| must fall below this (or flip sign) between episodes
XV_LEFT_CENSOR_MIN = 15    # no prior paired PM obs within this -> left-censored, excluded from primary
XV_SUPPORT_MIN = 0.80      # min PM probability mass captured by the Kalshi ladder (A2 support overlap)
XV_SKEW_SANITY_S = 120     # max |exchange_ts - received_at| to trust the WS exchange timestamp


# ---------------------------------------------------------------------------
# Pure lag core (unit-tested in tests/test_market_reaction_latency.py)
# ---------------------------------------------------------------------------
def reprice_onset(
    center_series: list[tuple[datetime, float]],
    official_ts: datetime,
    pre_min: int = PRE_MIN,
    post_min: int = POST_MIN,
    threshold: float = MATERIAL_MOVE_F,
) -> datetime | None:
    """First timestamp in (official_ts, official_ts+post_min] whose center deviates from the
    baseline by >= threshold. Baseline = last center at or before official_ts within pre_min.
    `center_series` must be sorted ascending by timestamp. Returns None if no baseline or no
    onset (interval-censored)."""
    baseline = None
    lo = official_ts - timedelta(minutes=pre_min)
    for ts, c in center_series:
        if lo <= ts <= official_ts:
            baseline = c
    if baseline is None:
        return None
    hi = official_ts + timedelta(minutes=post_min)
    for ts, c in center_series:
        if official_ts < ts <= hi and abs(c - baseline) >= threshold:
            return ts
    return None


def reprice_onset_window(
    center_series: list[tuple[datetime, float]],
    anchor: datetime,
    pre_min: int = PRE_MIN,
    post_min: int = POST_MIN,
    threshold: float = MATERIAL_MOVE_F,
) -> datetime | None:
    """Option A (model-run). Baseline = last center at or before (anchor - pre_min). Onset =
    first timestamp in (anchor - pre_min, anchor + post_min] deviating from baseline by >=
    threshold. Onset BEFORE `anchor` => negative lag (already priced before we saw it); onset
    after => positive. Returns None if no baseline or no move in the window."""
    base_time = anchor - timedelta(minutes=pre_min)
    baseline = None
    for ts, c in center_series:
        if ts <= base_time:
            baseline = c
    if baseline is None:
        return None
    hi = anchor + timedelta(minutes=post_min)
    for ts, c in center_series:
        if base_time < ts <= hi and abs(c - baseline) >= threshold:
            return ts
    return None


def _lag_minutes(onset: datetime, first_seen_at: datetime) -> float:
    return (onset - first_seen_at).total_seconds() / 60.0


# ---------------------------------------------------------------------------
# Cross-venue pure core (locked statistic, codex call 2026-06-09 / f2e7031;
# unit-tested in tests/test_market_reaction_latency.py)
# ---------------------------------------------------------------------------
def _effective_interval(lower, upper, typical_width: float) -> tuple[float, float] | None:
    """Half-open buckets get an effective width of `typical_width` (matches _bucket_mid)."""
    if lower is not None and upper is not None:
        return float(lower), float(upper)
    if lower is None and upper is not None:
        return float(upper) - typical_width, float(upper)
    if lower is not None and upper is None:
        return float(lower), float(lower) + typical_width
    return None


def rebin_to_ladder(
    src: list[tuple[float | None, float | None, float]],
    dst: list[tuple[float | None, float | None]],
    typical_width: float,
) -> tuple[list[float] | None, float]:
    """Re-bin probability mass from the source ladder to the destination ladder by interval
    overlap (uniform density within each source bucket). Returns (normalized dst probs,
    support_fraction = share of source mass captured by the dst ladder BEFORE normalizing).
    Returns (None, 0.0) when nothing maps."""
    dst_iv = [_effective_interval(lo, hi, typical_width) for lo, hi in dst]
    out = [0.0] * len(dst)
    total = 0.0
    for lo, hi, p in src:
        iv = _effective_interval(lo, hi, typical_width)
        if iv is None or p <= 0:
            continue
        s_lo, s_hi = iv
        width = s_hi - s_lo
        if width <= 0:
            continue
        total += p
        for i, d in enumerate(dst_iv):
            if d is None:
                continue
            ov = min(s_hi, d[1]) - max(s_lo, d[0])
            if ov > 0:
                out[i] += p * (ov / width)
    if total <= 0:
        return None, 0.0
    captured = sum(out)
    if captured <= 0:
        return None, 0.0
    return [x / captured for x in out], captured / total


def weighted_center(probs: list[float], mids: list[float]) -> float | None:
    tot = sum(probs)
    if tot <= 0:
        return None
    return sum(p * m for p, m in zip(probs, mids)) / tot


def directional_onset(
    center_series: list[tuple[datetime, float]],
    t0: datetime,
    direction: float,
    pre_min: int = PRE_MIN,
    post_min: int = POST_MIN,
    threshold: float = MATERIAL_MOVE_F,
) -> datetime | None:
    """Locked cross-venue onset: baseline = last Kalshi center at or before (t0 - pre_min);
    onset = first ts in (t0 - pre_min, t0 + post_min] where sign(direction) * (center -
    baseline) >= threshold. Onset before t0 = already priced (negative lag); after = led."""
    s = 1.0 if direction > 0 else -1.0
    base_time = t0 - timedelta(minutes=pre_min)
    baseline = None
    for ts, c in center_series:
        if ts <= base_time:
            baseline = c
    if baseline is None:
        return None
    hi = t0 + timedelta(minutes=post_min)
    for ts, c in center_series:
        if base_time < ts <= hi and s * (c - baseline) >= threshold:
            return ts
    return None


def _latest_at_or_before(series: list[tuple[datetime, float]], ts: datetime) -> float | None:
    val = None
    for t, c in series:
        if t > ts:
            break
        val = c
    return val


@dataclass
class XvEpisode:
    station: str
    valid_date: date
    t0: datetime
    gap0: float
    kind: str                  # "scored" | "left_censored" | "no_follow"
    lag_min: float | None      # set only when kind == "scored"
    gap_reduced: bool | None   # Kalshi moved toward PM by >= MATERIAL_MOVE_F from center(t0)


def crossvenue_episodes(
    station: str,
    valid_date: date,
    pm_obs: list[tuple[datetime, float]],
    kalshi_series: list[tuple[datetime, float]],
    gap_f: float = XV_GAP_F,
    rearm_f: float = XV_REARM_F,
    left_censor_min: int = XV_LEFT_CENSOR_MIN,
    post_min: int = POST_MIN,
) -> list[XvEpisode]:
    """Locked fresh-divergence-episode detector. A fresh episode starts at a paired PM
    observation t0 when |gap0| >= gap_f AND the prior paired PM observation was opposite-
    signed or inside the re-arm band (|gap| < rearm_f). Persistent above-threshold
    disagreement is ONE episode. No prior paired PM observation within left_censor_min
    minutes -> left-censored (reported separately, excluded from primary)."""
    episodes: list[XvEpisode] = []
    prev_gap: float | None = None
    prev_ts: datetime | None = None
    k_idx, k_n, k_latest = 0, len(kalshi_series), None
    for t0, poly_c in pm_obs:  # both series ascending: two-pointer k0 lookup
        while k_idx < k_n and kalshi_series[k_idx][0] <= t0:
            k_latest = kalshi_series[k_idx][1]
            k_idx += 1
        k0 = k_latest
        if k0 is None:
            continue
        gap = poly_c - k0
        try:
            if abs(gap) < gap_f:
                continue
            s = 1.0 if gap > 0 else -1.0
            if prev_ts is None or (t0 - prev_ts).total_seconds() / 60.0 > left_censor_min:
                episodes.append(XvEpisode(station, valid_date, t0, gap, "left_censored", None, None))
                continue
            assert prev_gap is not None
            armed = abs(prev_gap) < rearm_f or (prev_gap > 0) != (gap > 0)
            if not armed:
                continue  # continuation of the same divergence, not a new event
            onset = directional_onset(kalshi_series, t0, s)
            hi = t0 + timedelta(minutes=post_min)
            reduced = any(
                t0 < ts <= hi and s * (c - k0) >= MATERIAL_MOVE_F for ts, c in kalshi_series
            )
            if onset is None:
                episodes.append(XvEpisode(station, valid_date, t0, gap, "no_follow", None, reduced))
            else:
                episodes.append(
                    XvEpisode(station, valid_date, t0, gap, "scored", _lag_minutes(onset, t0), reduced)
                )
        finally:
            prev_gap, prev_ts = gap, t0
    return episodes


# ---------------------------------------------------------------------------
# DB access (VPS local DB only)
# ---------------------------------------------------------------------------
def _station_local_date(ts: datetime, tz_name: str) -> date:
    from zoneinfo import ZoneInfo

    return ts.astimezone(ZoneInfo(tz_name)).date()


def _bucket_mid(lower_f, upper_f, typical_width: float) -> float | None:
    if lower_f is not None and upper_f is not None:
        return (float(lower_f) + float(upper_f)) / 2.0
    if lower_f is None and upper_f is not None:
        return float(upper_f) - typical_width / 2.0
    if lower_f is not None and upper_f is None:
        return float(lower_f) + typical_width / 2.0
    return None


def _center_series(cur, station: str, valid_date: date) -> list[tuple[datetime, float]]:
    """Kalshi market-center time series for one station/date (lead-0 TMAX market)."""
    cur.execute(
        """
        SELECT ms.ts AS ts, ms.ticker AS ticker,
               ((ms.yes_ask::float + ms.yes_bid::float) / 2.0) AS p,
               km.lower_f AS lower_f, km.upper_f AS upper_f
          FROM market_snapshot ms
          JOIN kalshi_market km ON km.ticker = ms.ticker
         WHERE km.station = %(station)s AND km.valid_date = %(vd)s AND km.var = 'TMAX_DAILY'
           AND ms.yes_ask IS NOT NULL AND ms.yes_bid IS NOT NULL
         ORDER BY ms.ts
        """,
        {"station": station, "vd": valid_date},
    )
    rows = cur.fetchall()
    widths = [
        float(r["upper_f"]) - float(r["lower_f"])
        for r in rows
        if r["lower_f"] is not None and r["upper_f"] is not None and r["upper_f"] > r["lower_f"]
    ]
    typical = statistics.median(widths) if widths else 2.0
    by_ts: dict[datetime, list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        mid = _bucket_mid(r["lower_f"], r["upper_f"], typical)
        p = r["p"]
        if mid is None or p is None:
            continue
        by_ts[r["ts"]].append((float(p), mid))
    series: list[tuple[datetime, float]] = []
    for ts in sorted(by_ts):
        legs = by_ts[ts]
        tot = sum(p for p, _ in legs)
        if tot <= 0:
            continue
        center = sum(p * mid for p, mid in legs) / tot
        series.append((ts, center))
    return series


def _kalshi_ladder(cur, station: str, valid_date: date) -> tuple[list[tuple], float]:
    """The Kalshi bucket ladder (lower, upper) for one station/date + typical bucket width."""
    cur.execute(
        """
        SELECT lower_f, upper_f FROM kalshi_market
         WHERE station = %(s)s AND valid_date = %(vd)s AND var = 'TMAX_DAILY'
         ORDER BY COALESCE(lower_f, -1e9)
        """,
        {"s": station, "vd": valid_date},
    )
    rows = cur.fetchall()
    buckets = [(r["lower_f"], r["upper_f"]) for r in rows]
    widths = [
        float(hi) - float(lo) for lo, hi in buckets
        if lo is not None and hi is not None and float(hi) > float(lo)
    ]
    typical = statistics.median(widths) if widths else 2.0
    return buckets, typical


def _pm_center_obs(
    cur, station: str, valid_date: date, since: datetime,
    kalshi_buckets: list[tuple], typical: float,
) -> tuple[list[tuple[datetime, float]], dict]:
    """Forward Polymarket book observations -> (ts, poly center re-binned to the Kalshi
    ladder). One observation per snapshot batch ts. A2: substantial support overlap required."""
    cur.execute(
        """
        SELECT ts, lower_f, upper_f, yes_bid, yes_ask
          FROM external_market_snapshot
         WHERE venue = 'POLYMARKET' AND station = %(s)s AND valid_date = %(vd)s
           AND ts >= %(since)s
           AND yes_bid IS NOT NULL AND yes_ask IS NOT NULL
         ORDER BY ts
        """,
        {"s": station, "vd": valid_date, "since": since},
    )
    mids = [_bucket_mid(lo, hi, typical) for lo, hi in kalshi_buckets]
    diag = defaultdict(int)
    batches: dict[datetime, list[tuple]] = defaultdict(list)
    for r in cur.fetchall():
        batches[r["ts"]].append(r)
    out: list[tuple[datetime, float]] = []
    for ts in sorted(batches):
        src = []
        for r in batches[ts]:
            if r["lower_f"] is None and r["upper_f"] is None:
                continue
            p = (float(r["yes_bid"]) + float(r["yes_ask"])) / 2.0
            if p > 0:
                src.append((r["lower_f"], r["upper_f"], p))
        if not src:
            diag["pm_empty_batch"] += 1
            continue
        probs, support = rebin_to_ladder(src, kalshi_buckets, typical)
        if probs is None or support < XV_SUPPORT_MIN:
            diag["pm_support_overlap_skipped"] += 1
            continue
        center = weighted_center(probs, [m for m in mids if m is not None])
        if center is None:
            diag["pm_no_center"] += 1
            continue
        out.append((ts, center))
    diag["pm_observations"] = len(out)
    return out, dict(diag)


def _pm_ws_center_obs(
    cur, station: str, valid_date: date, since: datetime,
    kalshi_buckets: list[tuple], typical: float,
) -> list[tuple[datetime, float]]:
    """Polymarket center observations from the research-only WS stream (amendment A7).
    t0 = received_at (WeatherBot's genuine first sight of the book state, per the locked
    anchor). One observation per WS event once >= 3 buckets have state; re-binned to the
    Kalshi ladder with the same A2 support requirement as the polled path."""
    cur.execute(
        """
        SELECT received_at, asset_id, lower_f, upper_f, bid, ask
          FROM polymarket_ws_book_event
         WHERE station = %(s)s AND valid_date = %(vd)s AND received_at >= %(since)s
           AND bid IS NOT NULL AND ask IS NOT NULL
         ORDER BY received_at, id
        """,
        {"s": station, "vd": valid_date, "since": since},
    )
    mids = [_bucket_mid(lo, hi, typical) for lo, hi in kalshi_buckets]
    dst_mids = [m for m in mids if m is not None]
    state: dict[str, tuple] = {}
    out: list[tuple[datetime, float]] = []
    for r in cur.fetchall():
        if r["lower_f"] is None and r["upper_f"] is None:
            continue
        p = (float(r["bid"]) + float(r["ask"])) / 2.0
        if not (0.0 < p <= 1.0):
            continue
        state[r["asset_id"]] = (r["lower_f"], r["upper_f"], p)
        if len(state) < 3:
            continue
        probs, support = rebin_to_ladder(list(state.values()), kalshi_buckets, typical)
        if probs is None or support < XV_SUPPORT_MIN:
            continue
        center = weighted_center(probs, dst_mids)
        if center is not None:
            out.append((r["received_at"], center))
    return out


def _ws_center_series(cur, station: str, valid_date: date) -> list[tuple[datetime, float]]:
    """Kalshi center series from the research-only WebSocket top-of-book stream (A5).
    Timestamp = exchange ts when it passes the clock-skew sanity check, else receipt time.
    WS prices are integer cents; convert to dollars."""
    cur.execute(
        """
        SELECT w.received_at, w.exchange_ts, w.yes_bid, w.yes_ask,
               km.ticker, km.lower_f, km.upper_f
          FROM kalshi_ws_book_event w
          JOIN kalshi_market km ON km.ticker = w.ticker
         WHERE km.station = %(s)s AND km.valid_date = %(vd)s AND km.var = 'TMAX_DAILY'
           AND w.yes_bid IS NOT NULL AND w.yes_ask IS NOT NULL
         ORDER BY w.received_at, w.id
        """,
        {"s": station, "vd": valid_date},
    )
    rows = cur.fetchall()
    if not rows:
        return []
    widths = [
        float(r["upper_f"]) - float(r["lower_f"])
        for r in rows
        if r["lower_f"] is not None and r["upper_f"] is not None
    ]
    typical = statistics.median(widths) if widths else 2.0
    state: dict[str, tuple[float, float]] = {}
    series: list[tuple[datetime, float]] = []
    for r in rows:
        mid_f = _bucket_mid(r["lower_f"], r["upper_f"], typical)
        if mid_f is None:
            continue
        p = (float(r["yes_bid"]) + float(r["yes_ask"])) / 200.0  # cents -> dollars, then mid
        if not (0.0 <= p <= 1.0):
            continue
        ts = r["received_at"]
        ex = r["exchange_ts"]
        if ex is not None and abs((ex - ts).total_seconds()) <= XV_SKEW_SANITY_S:
            ts = ex
        state[r["ticker"]] = (p, mid_f)
        tot = sum(pp for pp, _ in state.values())
        if tot <= 0:
            continue
        series.append((ts, sum(pp * mm for pp, mm in state.values()) / tot))
    series.sort(key=lambda x: x[0])
    return series


def score_crossvenue(cur, since: datetime) -> tuple[list[LagRow], dict, list[XvEpisode]]:
    """Channel 4: fresh Polymarket divergence episode -> directional Kalshi follow-through."""
    cur.execute(
        """
        SELECT DISTINCT station, valid_date
          FROM external_market_snapshot
         WHERE venue = 'POLYMARKET' AND station = ANY(%(st)s)
           AND valid_date IS NOT NULL AND ts >= %(since)s
         ORDER BY valid_date, station
        """,
        {"st": list(CROSSVENUE_COMPARABLE), "since": since},
    )
    pairs = [(r["station"], r["valid_date"]) for r in cur.fetchall()]
    rows: list[LagRow] = []
    episodes: list[XvEpisode] = []
    diag = defaultdict(int)
    diag["station_dates"] = len(pairs)
    for station, vd in pairs:
        buckets, typical = _kalshi_ladder(cur, station, vd)
        if not buckets:
            diag["no_kalshi_ladder"] += 1
            continue
        pm_obs = _pm_ws_center_obs(cur, station, vd, since, buckets, typical)
        if pm_obs:
            diag["pm_ws_station_dates"] += 1
            diag["pm_observations"] += len(pm_obs)
        else:
            pm_obs, pm_diag = _pm_center_obs(cur, station, vd, since, buckets, typical)
            for k, v in pm_diag.items():
                diag[k] += v
            if pm_obs:
                diag["pm_polled_fallback_station_dates"] += 1
        if not pm_obs:
            continue
        series = _ws_center_series(cur, station, vd)
        if not series:
            series = _center_series(cur, station, vd)
            if series:
                diag["polling_fallback_station_dates"] += 1
        if not series:
            diag["no_kalshi_series"] += 1
            continue
        eps = crossvenue_episodes(station, vd, pm_obs, series)
        episodes.extend(eps)
        for e in eps:
            if e.kind == "scored" and e.lag_min is not None:
                rows.append(LagRow(e.station, e.valid_date, e.lag_min))
    for e in episodes:
        diag[f"episodes_{e.kind}"] += 1
    if episodes:
        diag["episodes_gap_reduced"] = sum(1 for e in episodes if e.gap_reduced)
        diag["episodes_poly_warmer"] = sum(1 for e in episodes if e.gap0 > 0)
        diag["episodes_poly_colder"] = sum(1 for e in episodes if e.gap0 < 0)
    return rows, dict(diag), episodes


def _provenance_events(cur, source_types: tuple[str, ...], since: datetime) -> list[dict]:
    cur.execute(
        """
        SELECT source_type, station, official_ts, first_seen_at
          FROM info_provenance
         WHERE source_type = ANY(%(src)s)
           AND first_seen_at >= %(since)s
           AND station IS NOT NULL
           AND official_ts IS NOT NULL
         ORDER BY first_seen_at
        """,
        {"src": list(source_types), "since": since},
    )
    return list(cur.fetchall())


# ---------------------------------------------------------------------------
# scoring + aggregation
# ---------------------------------------------------------------------------
@dataclass
class LagRow:
    station: str
    valid_date: date
    lag_min: float


def _station_tz(cur, station: str) -> str:
    cur.execute("SELECT tz FROM stations WHERE code = %(s)s", {"s": station})
    r = cur.fetchone()
    return (r and r["tz"]) or "America/New_York"


def score_channel(cur, cfg: dict, since: datetime) -> tuple[list[LagRow], dict]:
    events = _provenance_events(cur, cfg["sources"], since)
    series_cache: dict[tuple[str, date], list[tuple[datetime, float]]] = {}
    tz_cache: dict[str, str] = {}
    out: list[LagRow] = []
    diag = defaultdict(int)
    diag["events"] = len(events)
    for ev in events:
        st = ev["station"]
        ot = ev["official_ts"]
        fs = ev["first_seen_at"]
        # forward-genuineness guard: drop startup/stale backfill (amendment A3)
        if (fs - ot).total_seconds() / 60.0 > cfg["cap_min"]:
            diag["stale_backfill_excluded"] += 1
            continue
        tz = tz_cache.get(st) or tz_cache.setdefault(st, _station_tz(cur, st))
        # valid_date is the lead-0 market: local date of the genuine event time.
        vd = _station_local_date(ot, tz)
        key = (st, vd)
        if key not in series_cache:
            series_cache[key] = _center_series(cur, st, vd)
        series = series_cache[key]
        if not series:
            diag["no_market_series"] += 1
            continue
        if cfg["anchor"] == "first_seen_window":
            onset = reprice_onset_window(series, fs)   # Option A: anchor on first_seen
        else:
            onset = reprice_onset(series, ot)          # METAR/CLI: anchor on official_ts
        if onset is None:
            diag["no_onset_censored"] += 1
            continue
        out.append(LagRow(st, vd, _lag_minutes(onset, fs)))
    return out, dict(diag)


def _summarize(rows: list[LagRow]) -> dict:
    if not rows:
        return {"n": 0, "event_days": 0, "stations": 0}
    lags = [r.lag_min for r in rows]
    return {
        "n": len(rows),
        "event_days": len({(r.station, r.valid_date) for r in rows}),
        "stations": len({r.station for r in rows}),
        "median_lag": statistics.median(lags),
        "pos_frac": sum(1 for x in lags if x > 0) / len(lags),
    }


def _fmt(s: dict) -> str:
    if s["n"] == 0:
        return "n=0 (no scored events)"
    return (
        f"events={s['n']} event-days={s['event_days']} stations={s['stations']} "
        f"median_lag={s['median_lag']:+.2f}min pos_lag_frac={s['pos_frac']:.0%}"
    )


def _candidate(s: dict) -> bool:
    return (
        s["n"] > 0
        and s["median_lag"] >= CAND_MEDIAN_LAG_MIN
        and s["pos_frac"] >= CAND_POS_FRAC
        and s["event_days"] >= CAND_MIN_EVENT_DAYS
        and s["stations"] >= CAND_MIN_STATIONS
    )


def run(since: datetime, out_path: Path) -> None:
    from weather_bot.data import persistence

    lines = [
        "# EXP-2026-011 — Market Reaction Latency Results",
        "",
        f"_generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC_",
        "",
        "Implements `EXP_2026_011_MARKET_REACTION_LATENCY_AUDIT.md` (LOCKED). Measurement only;"
        " no trading change. Forward-collected `first_seen_at` only (since "
        f"{since:%Y-%m-%d %H:%M} UTC).",
        "",
        "lag = (first Kalshi reprice onset after official_ts) minus first_seen_at. "
        "POSITIVE lag = market repriced AFTER WeatherBot first saw the event (potential edge). "
        f"Reprice = |center move| >= {MATERIAL_MOVE_F:g} F. Polling is interval-censored: onset"
        " timing is an upper bound bounded by snapshot cadence.",
        "",
        f"Candidate gate (per channel): median lag >= {CAND_MEDIAN_LAG_MIN:g} min AND positive-"
        f"lag fraction >= {CAND_POS_FRAC:.0%} AND >= {CAND_MIN_EVENT_DAYS} event-days AND >= "
        f"{CAND_MIN_STATIONS} stations.",
        "",
    ]
    any_candidate = False
    with persistence.connect() as conn, conn.cursor() as cur:
        for channel, cfg in CHANNELS.items():
            rows, diag = score_channel(cur, cfg, since)
            overall = _summarize(rows)
            is_cand = _candidate(overall)
            any_candidate = any_candidate or is_cand
            lines.append(
                f"## Channel: {channel}  (sources: {', '.join(cfg['sources'])}; "
                f"anchor={cfg['anchor']}; forward-latency cap {cfg['cap_min']} min)"
            )
            lines.append("")
            lines.append(f"- overall: {_fmt(overall)}")
            lines.append(f"- diagnostics: {diag}")
            if overall["n"]:
                per_station = defaultdict(list)
                for r in rows:
                    per_station[r.station].append(r)
                for st in sorted(per_station):
                    lines.append(f"  - {st}: {_fmt(_summarize(per_station[st]))}")
            verdict = "CANDIDATE" if is_cand else (
                "insufficient sample" if overall["event_days"] < CAND_MIN_EVENT_DAYS else "no candidate"
            )
            lines.append(f"- **channel verdict: {verdict}**")
            lines.append("")

        # ---- Channel 4: cross-venue Polymarket lead (locked statistic, f2e7031) ----
        xv_rows, xv_diag, xv_eps = score_crossvenue(cur, since)
        xv_overall = _summarize(xv_rows)
        xv_cand = _candidate(xv_overall)
        any_candidate = any_candidate or xv_cand
        lines.append("## Channel: cross_venue (Polymarket lead) — channel 4")
        lines.append("")
        lines.append(
            f"Same-station map (amendment A2, expanded 2026-06-09 after rules verification — "
            f"see `EXP_2026_011_CROSS_VENUE_MAP_VERIFICATION.md`): comparable = "
            f"{', '.join(CROSSVENUE_COMPARABLE)}; excluded = {', '.join(CROSSVENUE_EXCLUDED)}; "
            "all others excluded until verified."
        )
        lines.append("")
        lines.append(
            f"Locked statistic: fresh divergence episode at PM observation t0 when "
            f"|poly_center − kalshi_center| >= {XV_GAP_F:g} F (re-arm band {XV_REARM_F:g} F / "
            f"sign flip; no prior paired PM obs within {XV_LEFT_CENSOR_MIN} min = left-censored, "
            "excluded from primary). Onset = first DIRECTIONAL Kalshi center move >= "
            f"{MATERIAL_MOVE_F:g} F toward the PM side vs the t0−{PRE_MIN}min baseline, searched "
            f"in (t0−{PRE_MIN}m, t0+{POST_MIN}m]. lag = onset − t0; onset before t0 = already "
            "priced (negative). PM centers re-binned to the Kalshi ladder (support overlap >= "
            f"{XV_SUPPORT_MIN:.0%}); Kalshi series from the WS top-of-book stream (exchange ts, "
            "skew-checked) with polling fallback. PM observation timing is itself poll-censored "
            "(~5 min cadence)."
        )
        lines.append("")
        lines.append(f"- overall (scored episodes): {_fmt(xv_overall)}")
        lines.append(f"- diagnostics: {xv_diag}")
        if xv_overall["n"]:
            xv_per_station = defaultdict(list)
            for r in xv_rows:
                xv_per_station[r.station].append(r)
            for st in sorted(xv_per_station):
                lines.append(f"  - {st}: {_fmt(_summarize(xv_per_station[st]))}")
        if xv_eps:
            gaps = sorted(abs(e.gap0) for e in xv_eps)
            lines.append(
                f"- episode |gap0|: median {gaps[len(gaps) // 2]:.2f} F, max {gaps[-1]:.2f} F"
            )
        xv_verdict = "CANDIDATE" if xv_cand else (
            "insufficient sample" if xv_overall["event_days"] < CAND_MIN_EVENT_DAYS else "no candidate"
        )
        lines.append(f"- **channel verdict: {xv_verdict}**")
        lines.append("")

    lines.append("## DSM channel")
    lines.append("")
    lines.append(
        "DSM is not first-class instrumented (no durable live table). Reported as "
        "not-yet-forward-instrumented per the handoff."
    )
    lines.append("")
    lines.append("## Audit status")
    lines.append("")
    if any_candidate:
        lines.append(
            "At least one channel meets the candidate gate. Per the locked decision rule this "
            "opens a SEPARATE paper-only signal pre-registration (EXP-2026-012). No trading "
            "change here; the candidate still requires the full promotion bar."
        )
    else:
        lines.append(
            "No channel meets the candidate gate yet. If event-days are below the threshold this "
            "is a forward-collection-in-progress run, not a closure. The latency axis is only "
            "closed once the committed forward window is reached with no candidate."
        )
    lines.append("")
    out_path.write_text("\n".join(lines))
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description="EXP-2026-011 market reaction latency (research-only).")
    ap.add_argument(
        "--since",
        required=True,
        help="ISO UTC cutoff; only first_seen_at >= this is used (forward-collected only).",
    )
    ap.add_argument("--out", default="research/reports/exp_2026_011_results.md")
    args = ap.parse_args()
    since = datetime.fromisoformat(args.since)
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    run(since, Path(args.out))


if __name__ == "__main__":
    main()
