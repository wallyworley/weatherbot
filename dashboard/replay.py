"""Counterfactual replay engine — re-score historical settled fills under
parameter overrides. Backs the Deep Dive tab.

This is the same logic used in the May 1 backtest when we validated the
1.10x widen change. Making it permanent so future tuning isn't a one-off
manual scripting exercise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import pytz

from weather_bot.config import KALSHI_FEE_COEFF, MAX_POSITION_PCT, MIN_EDGE_BPS, STATIONS
from weather_bot.data import persistence
from weather_bot.models.distribution import build_cdf_from_percentiles, _intraday_bounds


@dataclass
class ReplayParams:
    max_widen: float = 1.10            # current production
    hrrr_weight_cap: float = 0.95      # current production peaks at 0.95
    divergence_max: float = 0.50       # current production
    min_edge_bps: int = MIN_EDGE_BPS
    apply_hrrr_bias: bool = False      # current production: HRRR bias not applied
    bankroll_usd: float = 1000.0


def _hrrr_weight(h: int, cap: float) -> float:
    if h < 6: return 0.0
    if h <= 10: w = 0.2 + 0.1*(h-6)
    elif h <= 15: w = 0.6 + 0.06*(h-10)
    elif h <= 18: w = 0.9 + 0.017*(h-15)
    else: w = 0.95
    return min(cap, w)


def _fee_pc(price: float) -> float:
    return math.ceil(KALSHI_FEE_COEFF * price * (1.0 - price) * 100) / 100.0


def _kelly(p: float, b: float) -> float:
    if b <= 0: return 0.0
    return max(0.0, (b*p - (1.0 - p)) / b)


def _latest_nbm(station, valid_date, var, before_ts):
    sql = """SELECT percentile, value, run_time FROM prob_forecast
              WHERE station=%s AND valid_date=%s AND var=%s AND run_time<=%s
                AND run_time = (SELECT MAX(run_time) FROM prob_forecast
                                 WHERE station=%s AND valid_date=%s AND var=%s AND run_time<=%s)
              ORDER BY percentile"""
    with persistence.connect() as c, c.cursor() as cur:
        cur.execute(sql, (station, valid_date, var, before_ts,
                          station, valid_date, var, before_ts))
        return cur.fetchall()


def _latest_hrrr(station, valid_date, before_ts):
    sql = """SELECT MAX(value) AS tmax FROM det_forecast
              WHERE station=%s AND model='HRRR' AND var='TMP_2M'
                AND valid_time::date=%s AND run_time<=%s
                AND run_time = (SELECT MAX(run_time) FROM det_forecast
                                 WHERE station=%s AND model='HRRR' AND var='TMP_2M'
                                   AND valid_time::date=%s AND run_time<=%s)"""
    with persistence.connect() as c, c.cursor() as cur:
        cur.execute(sql, (station, valid_date, before_ts,
                          station, valid_date, before_ts))
        r = cur.fetchone()
        return r["tmax"] if r and r["tmax"] is not None else None


def _build_cdf(station, valid_date, var, ts, params: ReplayParams):
    rows = _latest_nbm(station, valid_date, var, ts)
    cdf = build_cdf_from_percentiles(rows)
    if cdf is None: return None
    lead = (valid_date - ts.date()).days
    month = valid_date.month
    bias = persistence.get_station_bias(station, "NBM_QMD", var, month, max(lead, 0))
    if bias:
        n = int(bias.get("sample_size") or 0)
        rb = float(bias["mean_bias_f"])
        rs = float(bias["stddev_f"])
        sh = n / (n + 10) if n > 0 else 0.0
        se = rs / (n ** 0.5) if n > 0 else float("inf")
        if abs(rb) < se: sh = 0.0
        st = 1.0
        rt = rows[0].get("run_time") if rows else None
        if rt is not None:
            ah = (ts - rt).total_seconds() / 3600.0
            if ah > 8: st = max(0.0, 1 - (ah - 8) / 10)
        cdf.shift -= sh * rb * st
        if rs > 0 and len(cdf.values) >= 2:
            p90 = float(np.interp(0.9, cdf.probs, cdf.values))
            p10 = float(np.interp(0.1, cdf.probs, cdf.values))
            cs = (p90 - p10) / 2.56
            if cs > 0 and rs > cs:
                sc = min(rs / cs, params.max_widen)
                med = float(np.interp(0.5, cdf.probs, cdf.values))
                cdf.values = med + sc * (cdf.values - med)
    if lead == 0 and var == "TMAX_DAILY":
        hv = _latest_hrrr(station, valid_date, ts)
        if hv is not None:
            if params.apply_hrrr_bias:
                hb = persistence.get_station_bias(station, "HRRR", var, month, 0)
                if hb: hv -= float(hb["mean_bias_f"])
            tz = pytz.timezone(STATIONS[station].tz)
            w = _hrrr_weight(ts.astimezone(tz).hour, params.hrrr_weight_cap)
            if w > 0:
                cdf.shift += w * (hv - cdf.median())
    if lead == 0:
        tx, tn = _intraday_bounds(station, valid_date, ts)
        if var == "TMAX_DAILY" and tx is not None: cdf.floor = float(tx)
        elif var == "TMIN_DAILY" and tn is not None: cdf.ceiling = float(tn)
    return cdf


def replay(start: date, end: date, params: ReplayParams,
           station_filter: Optional[str] = None) -> pd.DataFrame:
    """Replay every settled fill in [start, end] under params.

    Returns one row per fill with both the recorded outcome and the
    counterfactual (would-have-opened, new-fair-prob, new-expected-edge).
    """
    sql = """
    SELECT pf.id, pf.ts, pf.ticker, pf.side AS rec_side, pf.price, pf.contracts,
           pf.fees, pf.payout, km.station, km.var, km.valid_date, km.lower_f, km.upper_f,
           s.fair_prob AS old_fair, s.market_ask, s.market_bid
      FROM paper_fill pf
      JOIN kalshi_market km ON km.ticker = pf.ticker
      LEFT JOIN signal s ON s.id = pf.signal_id
     WHERE pf.settled = TRUE
       AND km.valid_date BETWEEN %s AND %s
       AND (%s::text IS NULL OR km.station = %s)
     ORDER BY pf.ts
    """
    with persistence.connect() as c, c.cursor() as cur:
        cur.execute(sql, (start, end, station_filter, station_filter))
        fills = cur.fetchall()

    out_rows = []
    for f in fills:
        ts = f["ts"]
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        cdf = _build_cdf(f["station"], f["valid_date"], f["var"], ts, params)
        if cdf is None:
            continue
        new_p_yes = cdf.prob_between(f["lower_f"], f["upper_f"])
        # Evaluate both sides
        ya = float(f["market_ask"]) if f["market_ask"] is not None else None
        yb = float(f["market_bid"]) if f["market_bid"] is not None else None
        yes_mid = (ya + yb) / 2 if ya is not None and yb is not None else None
        div_skip = yes_mid is not None and abs(new_p_yes - yes_mid) > params.divergence_max

        best_action, best_side, best_eb, best_size = "SKIP", None, None, 0.0
        for side in ("YES", "NO"):
            price = ya if side == "YES" else (None if yb is None else 1.0 - yb)
            wp = new_p_yes if side == "YES" else 1.0 - new_p_yes
            if price is None or price <= 0 or price >= 1: continue
            fee = _fee_pc(price)
            ev_c = wp * (1 - price) - (1 - wp) * price - fee
            eb = int(ev_c * 10000 / max(price, 1e-6))
            b = (1 - price) / price
            k = min(_kelly(wp, b) * 0.25, MAX_POSITION_PCT)
            size = max(0.0, k * params.bankroll_usd)
            fl = fee / price
            if div_skip: action = "SKIP_DIV"
            elif fl > 0.20: action = "SKIP_FEE"
            elif not (ev_c > 0 and eb >= params.min_edge_bps and size >= 1.0): action = "SKIP_EDGE"
            else: action = "OPEN"
            if action == "OPEN" and (best_action != "OPEN" or eb > (best_eb or -1)):
                best_action, best_side, best_eb, best_size = action, side, eb, size

        # Recorded P&L at original size
        rec_pnl = (float(f["payout"] or 0) - float(f["price"])) * f["contracts"] - float(f["fees"])
        actual = 1.0 if (float(f["payout"] or 0) > 0) else 0.0

        # Counterfactual P&L: recompute size + fees if we'd still take same side
        if best_action == "OPEN" and best_side == f["rec_side"]:
            new_price = float(f["price"])
            new_contracts = max(1, int(best_size / new_price))
            new_fees = _fee_pc(new_price) * new_contracts
            new_pnl = (float(f["payout"] or 0) - new_price) * new_contracts - new_fees
        else:
            new_pnl = 0.0  # would have skipped or flipped → no fill

        out_rows.append({
            "ticker": f["ticker"],
            "valid_date": f["valid_date"],
            "station": f["station"],
            "rec_side": f["rec_side"],
            "rec_price": float(f["price"]),
            "rec_contracts": f["contracts"],
            "rec_pnl": rec_pnl,
            "won": bool(actual),
            "old_fair": float(f["old_fair"]) if f["old_fair"] is not None else None,
            "new_fair": new_p_yes if f["rec_side"] == "YES" else 1.0 - new_p_yes,
            "new_action": best_action,
            "new_side": best_side,
            "new_edge_bps": best_eb,
            "new_pnl": new_pnl,
            "brier_recorded": ((float(f["old_fair"]) if f["rec_side"] == "YES" else 1.0 - float(f["old_fair"])) - actual)**2 if f["old_fair"] is not None else None,
            "brier_replay":   ((new_p_yes if f["rec_side"] == "YES" else 1.0 - new_p_yes) - actual)**2,
        })
    return pd.DataFrame(out_rows)
