"""Plain-English translations for bot jargon shown in the dashboard.

The technical names (BIAS_GATE, lead_day, fair_prob) are useful in the code
but unfriendly in the UI. Everything that gets shown to a human reader
should go through these helpers.

Keep additions short and goal-oriented: the rule of thumb is "would a
non-developer who knows what Kalshi is understand this sentence?"
"""
from __future__ import annotations

# Skip reason codes from main.py / strategy/ev.py → user-facing phrase + emoji.
# Order roughly matches frequency in production.
SKIP_REASON_PLAIN: dict[str, tuple[str, str]] = {
    "NO_EDGE":          ("📊", "Market priced fairly — no opportunity"),
    "LEAD_DAY_GATE":    ("📅", "Bot only bets same-day, not tomorrow"),
    "BIAS_GATE":        ("🏗️", "Not enough data for this city yet"),
    "FEE_LOAD":         ("💸", "Trading fees would eat the profit"),
    "TRIPWIRE_RED":     ("🛑", "Bot temporarily paused on this city"),
    "DIVERGENCE":       ("⚠️", "Bot and market disagree wildly — being cautious"),
    "INTRADAY_SQUEEZE": ("🕐", "Late in the day, temperature already settled"),
    "NO_FADE_GATE":     ("🤝", "Bot disagreed with market too strongly — passed"),
    "PROFIT_GATE":      ("🎯", "Expected profit below threshold for this cell"),
    "NO_BOOK":          ("📭", "Kalshi had no live quotes on this market"),
    "AGREEMENT":        ("👥", "Required model agreement not met"),
    "UNCLASSIFIED":     ("❓", "Other / unclassified"),
}


def skip_reason_plain(code: str | None) -> tuple[str, str]:
    """Returns (emoji, plain_english) for a skip_reason code."""
    if not code:
        return ("❓", "Unknown")
    return SKIP_REASON_PLAIN.get(code, ("❓", code))


# Station code → friendly city name for display.
STATION_FRIENDLY: dict[str, str] = {
    "KNYC": "New York (Central Park)",
    "KLGA": "New York (LaGuardia)",
    "KMDW": "Chicago (Midway)",
    "KORD": "Chicago (O'Hare)",
    "KMIA": "Miami",
    "KLAX": "Los Angeles",
    "KDEN": "Denver",
    "KATL": "Atlanta",
    "KAUS": "Austin",
    "KPHL": "Philadelphia",
    "KDCA": "Washington DC",
    "KBOS": "Boston",
    "KPHX": "Phoenix",
    "KDFW": "Dallas-Fort Worth",
    "KSFO": "San Francisco",
    "KSEA": "Seattle",
    "KLAS": "Las Vegas",
    "KMSY": "New Orleans",
    "KMSP": "Minneapolis",
    "KSAT": "San Antonio",
    "KOKC": "Oklahoma City",
}


def friendly_station(code: str) -> str:
    return STATION_FRIENDLY.get(code, code)


def friendly_var(var: str) -> str:
    if var == "TMAX_DAILY":
        return "daily high"
    if var == "TMIN_DAILY":
        return "daily low"
    return var


def lead_day_phrase(lead_day: int | None) -> str:
    if lead_day is None:
        return ""
    if lead_day == 0:
        return "today"
    if lead_day == 1:
        return "tomorrow"
    return f"{lead_day} days out"


def bucket_phrase(lower_f: float | None, upper_f: float | None) -> str:
    """Same as the dashboard's _bucket_label, but free-standing.

    upper_f is stored as exclusive (hi+1) by the parser, so subtract 1
    for display. Matches Kalshi wording exactly:
       "79° to 80°", "78° or below", "87° or above".
    """
    lo, hi = lower_f, upper_f
    if lo is None and hi is None:
        return "?"
    if lo is None:
        return f"{hi - 1:.0f}° or below"
    if hi is None:
        return f"{lo:.0f}° or above"
    return f"{lo:.0f}° to {hi - 1:.0f}°"


def confidence_phrase(fair_prob: float | None) -> str:
    """E.g. 0.34 → 'bot is 34% confident'."""
    if fair_prob is None:
        return ""
    return f"bot is {fair_prob * 100:.0f}% confident"


def market_implied_phrase(yes_ask: float | None, side: str) -> str:
    """Translate the market price into 'market thinks X% likely'."""
    if yes_ask is None:
        return ""
    pct = yes_ask * 100 if side == "YES" else (1.0 - yes_ask) * 100
    return f"market priced it at {pct:.0f}%"


def usd(amount: float | None, plus_sign: bool = False) -> str:
    """Format a dollar amount, e.g. -$13.93 or +$1.35."""
    if amount is None:
        return "—"
    sign = ""
    if plus_sign and amount > 0:
        sign = "+"
    if amount < 0:
        return f"−${abs(amount):,.2f}"
    return f"{sign}${amount:,.2f}"


def signed_color(amount: float | None) -> str:
    """Return a CSS color for a P&L number: green positive, red negative."""
    if amount is None or amount == 0:
        return "#737373"
    return "#16a34a" if amount > 0 else "#dc2626"
