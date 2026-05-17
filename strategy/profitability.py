"""Profitability controls layered on top of the forecast signal.

These controls do not invent a new weather model. They shape entry size or
block entries in slices where corrected-fee paper trading has been weak.
"""
from __future__ import annotations

from datetime import date, datetime

from weather_bot.config import (
    KNYC_L1_SIZE_MULT,
    NO_UNDER_50C_SIZE_MULT,
    PAPER_BYPASS_STATION_PAUSE,
    PAPER_MODE,
    PAUSED_TRADE_STATIONS,
    PROFIT_CONTROLS_ENABLED,
    YES_10_25C_MAX_USD,
    YES_10_25C_SIZE_MULT,
    YES_25_50C_SIZE_MULT,
    YES_UNDER_10C_SIZE_MULT,
)
from weather_bot.models.distribution import lead_day_for_station
from weather_bot.strategy.ev import Signal


def chosen_price(sig: Signal) -> float | None:
    """Return the entry price for the side selected in `sig`."""
    if sig.side == "YES":
        return sig.market_ask
    if sig.market_bid is None:
        return None
    return 1.0 - sig.market_bid


def apply_profitability_controls(
    sig: Signal,
    station: str,
    valid_date: date,
    now_utc: datetime,
) -> Signal:
    """Mutate and return a signal with profitability gates/sizing applied."""
    if not PROFIT_CONTROLS_ENABLED or sig.action != "OPEN":
        return sig

    station_u = station.upper()
    lead_day = max(0, lead_day_for_station(station_u, valid_date, now_utc))
    price = chosen_price(sig)

    reasons: list[str] = []
    if station_u in PAUSED_TRADE_STATIONS:
        if PAPER_MODE and PAPER_BYPASS_STATION_PAUSE:
            sig.notes = f"PAUSE_BYPASS_PAPER|station={station_u} {sig.notes}"
        else:
            sig.action = "SKIP"
            sig.skip_reason = "PROFIT_GATE"
            sig.notes = f"PROFIT_GATE|station_paused={station_u} {sig.notes}"
            return sig

    multiplier = 1.0
    if station_u == "KNYC" and lead_day >= 1:
        multiplier *= KNYC_L1_SIZE_MULT
        reasons.append(f"KNYC_L{lead_day}x{KNYC_L1_SIZE_MULT:.2f}")

    max_size_usd: float | None = None
    if price is not None:
        if sig.side == "NO" and price < 0.50:
            multiplier *= NO_UNDER_50C_SIZE_MULT
            reasons.append(f"NO_under_50cx{NO_UNDER_50C_SIZE_MULT:.2f}")
        if sig.side == "YES" and price < 0.10:
            multiplier *= YES_UNDER_10C_SIZE_MULT
            reasons.append(f"YES_under_10cx{YES_UNDER_10C_SIZE_MULT:.2f}")
        if sig.side == "YES" and 0.10 <= price < 0.25:
            multiplier *= YES_10_25C_SIZE_MULT
            max_size_usd = YES_10_25C_MAX_USD
            reasons.append(f"YES_10_25cx{YES_10_25C_SIZE_MULT:.2f}")
        if sig.side == "YES" and 0.25 <= price < 0.50:
            multiplier *= YES_25_50C_SIZE_MULT
            reasons.append(f"YES_25_50cx{YES_25_50C_SIZE_MULT:.2f}")

    if multiplier <= 0:
        sig.action = "SKIP"
        sig.skip_reason = "PROFIT_GATE"
        sig.notes = f"PROFIT_GATE|blocked_slice|rules={','.join(reasons)} {sig.notes}"
        return sig

    if multiplier < 1.0:
        sig.size_usd *= multiplier
        sig.kelly_fraction *= multiplier
        sig.notes = f"PROFIT_SIZE|mult={multiplier:.2f}|rules={','.join(reasons)} {sig.notes}"

    if max_size_usd is not None and sig.size_usd > max_size_usd:
        sig.size_usd = max_size_usd
        sig.notes = f"PROFIT_CAP|max_usd={max_size_usd:.2f} {sig.notes}"

    if sig.size_usd < 1.0:
        sig.action = "SKIP"
        sig.skip_reason = "PROFIT_GATE"
        sig.notes = f"PROFIT_GATE|size_below_min {sig.notes}"

    return sig
