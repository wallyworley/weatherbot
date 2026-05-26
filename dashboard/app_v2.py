"""weather_bot dashboard, redesigned for plain-English readability.

Run:
    streamlit run dashboard/app_v2.py --server.address 127.0.0.1 --server.port 8502

Designed to live alongside the original dashboard (app.py, port 8501) — both
can run at the same time. This one prioritizes a non-technical reader: every
page leads with money + outcomes, and bot internals are translated or hidden
behind explicit "show your work" expanders.

Pages:
  • Today                  — morning/midday glance: are we OK, what's at stake?
  • Trade Log              — every bet, plain English, with click-to-reveal jargon
  • How is the bot doing?  — weekly review: where's the edge, where's the bleed?
  • Engine Room            — technical diagnostics for debugging sessions

Reads queries.py for all DB access; translations.py for all jargon mapping.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Iterable

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from weather_bot.data.persistence import connect
from weather_bot.dashboard import queries, translations as t

# PnL formula that mirrors queries.REALIZED_PNL_SQL and handles BOTH paths
# the bot uses to close a paper fill:
#   - Early exit (close_paper_fill_early): exit_price + exit_fees populated,
#     payout is NULL.
#   - Held to settlement: payout populated, exit_price NULL.
# The bot's strategy/early_exits.py harvests winners at 0.85 threshold and is
# a meaningful chunk of net P&L (~+$190/30d). v2 previously used a simpler
# (payout - price)*c - fees formula that silently dropped early exits; this
# CASE form fixes that.
_V2_PNL_SQL = """
CASE
    WHEN pf.exit_price IS NOT NULL
        THEN (pf.exit_price - pf.price) * pf.contracts - pf.fees
             - COALESCE(pf.exit_fees, 0)
    WHEN pf.payout IS NOT NULL
        THEN (pf.payout - pf.price) * pf.contracts - pf.fees
    ELSE NULL
END
"""

st.set_page_config(page_title="weather_bot · v2", layout="wide", page_icon="🌡️")

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
st.markdown("""
<style>
  .block-container { padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1280px; }
  h1, h2, h3 { font-weight: 600; }
  /* Top metric cards */
  .v2-card {
    border: 1px solid rgba(128,128,128,0.22);
    border-radius: 10px;
    padding: 1rem 1.1rem;
    background: rgba(128,128,128,0.04);
    min-height: 96px;
  }
  .v2-card-label {
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em;
    color: rgba(128,128,128,0.95); margin-bottom: 0.4rem;
  }
  .v2-card-value { font-size: 1.85rem; font-weight: 700; line-height: 1.1; }
  .v2-card-sub { font-size: 0.85rem; color: rgba(128,128,128,0.85); margin-top: 0.35rem; }
  /* Forecast / position rows */
  .v2-row {
    border: 1px solid rgba(128,128,128,0.18);
    border-radius: 8px; padding: 0.85rem 1rem; margin-bottom: 0.55rem;
    background: rgba(128,128,128,0.03);
  }
  .v2-row-title { font-weight: 600; font-size: 1rem; margin-bottom: 0.35rem; }
  .v2-row-line { font-size: 0.92rem; line-height: 1.5; color: rgba(128,128,128,0.95); }
  .v2-row-line strong { color: inherit; }
  /* Callouts */
  .v2-callout {
    border-left: 5px solid var(--color);
    background: rgba(128,128,128,0.06);
    padding: 0.85rem 1rem; border-radius: 6px; margin: 0.5rem 0;
  }
  .v2-callout-title { font-weight: 600; margin-bottom: 0.2rem; }
  /* Skip-reason row */
  .v2-skip { display: flex; gap: 0.75rem; align-items: baseline;
             padding: 0.35rem 0; border-bottom: 1px dashed rgba(128,128,128,0.18); }
  .v2-skip:last-child { border-bottom: none; }
  .v2-skip-emoji { font-size: 1.1rem; min-width: 1.5rem; }
  .v2-skip-text { flex: 1; }
  .v2-skip-count { font-variant-numeric: tabular-nums; color: rgba(128,128,128,0.8); font-size: 0.9rem; }
  /* Bet pill */
  .v2-pill { display: inline-block; padding: 0.1rem 0.55rem; border-radius: 999px;
             font-size: 0.78rem; font-weight: 600; margin-right: 0.35rem; }
  .v2-pill-yes { background: rgba(22,163,74,0.15); color: #16a34a; }
  .v2-pill-no  { background: rgba(220,38,38,0.15); color: #dc2626; }
  .v2-pill-pending { background: rgba(245,158,11,0.15); color: #f59e0b; }
  .v2-pill-filled  { background: rgba(99,102,241,0.15); color: #6366f1; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Render helpers (presentation only — no DB calls here)
# ---------------------------------------------------------------------------
_INDENT_RE = re.compile(r'^[ \t]+', re.MULTILINE)


def _md(html: str) -> None:
    """Render raw HTML through st.markdown safely.

    Streamlit's markdown processor treats any line indented 4+ spaces as a
    code block — even with unsafe_allow_html=True. f-strings that pretty-
    print HTML across multiple indented lines therefore render their
    closing tags as visible text. Strip leading whitespace from every line
    before handing the string to streamlit.
    """
    st.markdown(_INDENT_RE.sub('', html), unsafe_allow_html=True)


def big_card(label: str, value: str, sub: str = "", value_color: str | None = None) -> None:
    color_style = f"color:{value_color};" if value_color else ""
    _md(f"""<div class="v2-card">
              <div class="v2-card-label">{label}</div>
              <div class="v2-card-value" style="{color_style}">{value}</div>
              <div class="v2-card-sub">{sub}</div>
            </div>""")


def callout(title: str, body: str, color: str = "#16a34a") -> None:
    _md(f"""<div class="v2-callout" style="--color:{color};">
              <div class="v2-callout-title">{title}</div>
              <div>{body}</div>
            </div>""")


def section(title: str, hint: str | None = None) -> None:
    st.markdown(f"### {title}")
    if hint:
        st.caption(hint)


def side_pill(side: str) -> str:
    cls = "v2-pill-yes" if side == "YES" else "v2-pill-no"
    return f'<span class="v2-pill {cls}">{side}</span>'


def status_pill(label: str) -> str:
    cls = "v2-pill-filled" if "Filled" in label else "v2-pill-pending"
    return f'<span class="v2-pill {cls}">{label}</span>'


# ---------------------------------------------------------------------------
# v2 DB helpers (self-contained — don't require optional schema columns)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=12)
def _v2_settled_fills(days_back: int) -> pd.DataFrame:
    """One row per fill in the last N days. Includes both held-to-settlement
    fills (payout populated) and early-exit fills (exit_price populated).
    PnL uses _V2_PNL_SQL which handles both paths."""
    sql = f"""
        SELECT pf.id, pf.ts AS fill_ts, pf.ticker, pf.side, pf.price,
               pf.contracts, pf.fees, pf.payout, pf.exit_price, pf.exit_fees,
               pf.exit_ts, pf.exit_reason, pf.settled,
               km.station, km.var, km.valid_date, km.lower_f, km.upper_f,
               s.fair_prob, s.market_ask, s.market_bid,
               GREATEST(0, (km.valid_date - (pf.ts AT TIME ZONE st.tz)::date))
                   AS lead_day,
               CASE WHEN pf.exit_price IS NOT NULL THEN 'EARLY_EXIT'
                    WHEN pf.payout IS NOT NULL THEN 'SETTLED'
                    ELSE 'OPEN' END AS close_path,
               {_V2_PNL_SQL} AS realized_pnl,
               ((CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1.0 - s.fair_prob END)
                  * (1 - pf.price)
                - (1 - (CASE WHEN pf.side='YES' THEN s.fair_prob
                              ELSE 1.0 - s.fair_prob END)) * pf.price)
                * pf.contracts - pf.fees AS expected_pnl
          FROM paper_fill pf
          JOIN kalshi_market km ON km.ticker = pf.ticker
          JOIN stations st ON st.code = km.station
          LEFT JOIN signal s ON s.id = pf.signal_id
         WHERE km.valid_date >= CURRENT_DATE - (%s || ' days')::interval
         ORDER BY pf.ts DESC
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (days_back,))
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=12)
def early_exit_summary(days_back: int = 30) -> dict:
    """Early-exit firing rate + counterfactual vs held-to-settlement.

    Returns dict with: exit_count, exit_pnl, settled_count, settled_pnl,
    avg_progress_captured, leftover_estimate.
    """
    df = _v2_settled_fills(days_back=days_back)
    if df.empty:
        return {"exit_count": 0, "exit_pnl": 0.0,
                "settled_count": 0, "settled_pnl": 0.0,
                "exit_share_of_pnl": 0.0}
    settled = df[df["settled"] == True]  # noqa: E712
    exits = settled[settled["close_path"] == "EARLY_EXIT"]
    held = settled[settled["close_path"] == "SETTLED"]
    exit_pnl = float(exits["realized_pnl"].sum()) if not exits.empty else 0.0
    held_pnl = float(held["realized_pnl"].sum()) if not held.empty else 0.0
    total = exit_pnl + held_pnl
    share = (exit_pnl / total * 100.0) if total != 0 else 0.0
    return {
        "exit_count": int(len(exits)),
        "exit_pnl": exit_pnl,
        "settled_count": int(len(held)),
        "settled_pnl": held_pnl,
        "exit_share_of_pnl": share,
    }


def pnl_yesterday() -> dict:
    """Yesterday's settled net P&L."""
    df = _v2_settled_fills(days_back=2)
    if df.empty:
        return {"net": None, "n_fills": 0, "n_wins": 0}
    settled = df[(df["settled"] == True) &  # noqa: E712
                  (pd.to_datetime(df["valid_date"]).dt.date ==
                   (datetime.now().date() - timedelta(days=1)))]
    if settled.empty:
        return {"net": None, "n_fills": 0, "n_wins": 0}
    net = float(settled["realized_pnl"].sum())
    wins = int((settled["realized_pnl"] > 0).sum())
    return {"net": net, "n_fills": len(settled), "n_wins": wins}


def pnl_this_week() -> dict:
    """Sum of realized P&L over the last 7 calendar days (settled fills)."""
    df = _v2_settled_fills(days_back=7)
    if df.empty:
        return {"net": 0.0, "n_fills": 0, "n_wins": 0, "n_losses": 0}
    settled = df[df["settled"] == True]  # noqa: E712
    if settled.empty:
        return {"net": 0.0, "n_fills": 0, "n_wins": 0, "n_losses": 0}
    net = float(settled["realized_pnl"].sum())
    wins = int((settled["realized_pnl"] > 0).sum())
    losses = int((settled["realized_pnl"] <= 0).sum())
    return {"net": net, "n_fills": len(settled), "n_wins": wins, "n_losses": losses}


def cumulative_pnl_series(days_back: int = 30) -> pd.DataFrame:
    """Returns daily settled-P&L cumulative sum, indexed by calendar date."""
    df = _v2_settled_fills(days_back=days_back)
    if df.empty:
        return pd.DataFrame(columns=["day", "daily_pnl", "cumulative_pnl"])
    settled = df[df["settled"] == True].copy()  # noqa: E712
    if settled.empty:
        return pd.DataFrame(columns=["day", "daily_pnl", "cumulative_pnl"])
    settled["day"] = pd.to_datetime(settled["valid_date"]).dt.date
    daily = settled.groupby("day", as_index=False)["realized_pnl"].sum()
    daily = daily.rename(columns={"realized_pnl": "daily_pnl"})
    daily = daily.sort_values("day")
    daily["cumulative_pnl"] = daily["daily_pnl"].cumsum()
    return daily


def pnl_cell_grid(days_back: int = 30) -> pd.DataFrame:
    """Aggregate settled fills to (station, side, lead_phrase) → net_pnl."""
    df = _v2_settled_fills(days_back=days_back)
    if df.empty:
        return df
    settled = df[df["settled"] == True].copy()  # noqa: E712
    if settled.empty:
        return pd.DataFrame()
    settled["lead_phrase"] = settled["lead_day"].map(
        lambda d: "Same day" if d == 0 else ("Day ahead" if d == 1 else f"{int(d)}d out")
    )
    grouped = (settled.groupby(["station", "side", "lead_phrase"], as_index=False)
                       .agg(net_pnl=("realized_pnl", "sum"),
                            fills=("id", "count"),
                            wins=("realized_pnl", lambda s: int((s > 0).sum()))))
    grouped["win_rate"] = grouped["wins"] / grouped["fills"]
    grouped["city"] = grouped["station"].map(t.friendly_station)
    grouped["cell"] = grouped["lead_phrase"] + " " + grouped["side"]
    return grouped


def overall_bot_status() -> tuple[str, str, str]:
    """Returns (emoji, label, color) summarizing the bot's operational health.

    Aggregates: data fetchers, latest signal generation, and any RED stations.
    """
    try:
        health = queries.latest_health()
    except Exception:
        return ("⚪", "Unknown", "#737373")
    if health.empty:
        return ("⚪", "Unknown", "#737373")
    statuses = health["status"].astype(str).str.upper()
    red_count = int((statuses == "RED").sum())
    amber_count = int((statuses == "AMBER").sum())
    if red_count > 0:
        return ("🔴", f"{red_count} issue{'s' if red_count != 1 else ''}", "#dc2626")
    if amber_count > 0:
        return ("🟡", f"{amber_count} warning{'s' if amber_count != 1 else ''}", "#f59e0b")
    return ("🟢", "Healthy", "#16a34a")


def latest_forecast_for(station: str, valid_date) -> dict | None:
    """Returns {p10, p25, p50, p75, p90} from the most recent NBM cycle."""
    inputs = queries.latest_distribution_inputs(station, valid_date, "TMAX_DAILY")
    nbm = inputs.get("nbm")
    if nbm is None or nbm.empty:
        return None
    out = {}
    for _, row in nbm.iterrows():
        out[int(row["percentile"])] = float(row["value"])
    if 50 not in out:
        return None
    return out


@st.cache_data(ttl=60)
def observed_high_today(station: str) -> float | None:
    """Highest METAR temperature observed today (station-local date).

    We use metar_obs rather than cli_obs because CLI isn't issued until
    early next morning — useless for in-day "where are we now" context.
    HFMETAR (5-min observations) gives us a much more current picture than
    hourly METAR for ASOS stations.
    """
    sql = """
        SELECT MAX(temp_f) AS running_max
          FROM metar_obs m
          JOIN stations st ON st.code = m.station
         WHERE m.station = %s
           AND (m.obs_time AT TIME ZONE st.tz)::date = CURRENT_DATE
    """
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(sql, (station,))
            row = cur.fetchone()
            if row and row.get("running_max") is not None:
                return float(row["running_max"])
    except Exception:
        return None
    return None


def market_p50_for(station: str, valid_date) -> float | None:
    """Best estimate of where Kalshi thinks the temperature will land — the
    midpoint of the bucket whose YES side trades closest to 50 cents."""
    buckets = queries.kalshi_buckets_today(station, valid_date, "TMAX_DAILY")
    if buckets.empty:
        return None
    # Find bucket with best yes_ask near 0.5. Use the latest market snapshot.
    # Cheap heuristic: take the midpoint of all closed-form buckets weighted
    # equally — good enough for a single sentence.
    closed = buckets.dropna(subset=["lower_f", "upper_f"])
    if closed.empty:
        return None
    midpoints = (closed["lower_f"] + closed["upper_f"] - 1) / 2.0
    return float(midpoints.mean())


# ---------------------------------------------------------------------------
# PAGE: TODAY
# ---------------------------------------------------------------------------
def page_today() -> None:
    today_str = datetime.now().strftime("%A, %B %-d, %Y")
    st.title("Today")
    st.caption(today_str)

    # ── Top metric cards ──────────────────────────────────────────────────
    y = pnl_yesterday()
    w = pnl_this_week()
    status_emoji, status_label, status_color = overall_bot_status()

    c1, c2, c3 = st.columns(3)
    with c1:
        net = y.get("net")
        if net is None or y.get("n_fills", 0) == 0:
            big_card("Yesterday", "—", "No bets settled yesterday.")
        else:
            losses = y["n_fills"] - y["n_wins"]
            big_card(
                "Yesterday",
                t.usd(net, plus_sign=True),
                f"{y['n_wins']} wins, {losses} losses",
                value_color=t.signed_color(net),
            )
    with c2:
        if w["n_fills"] == 0:
            big_card("Last 7 days", "—", "No settled bets in the last week.")
        else:
            big_card(
                "Last 7 days",
                t.usd(w["net"], plus_sign=True),
                f"{w['n_wins']} wins, {w['n_losses']} losses",
                value_color=t.signed_color(w["net"]),
            )
    with c3:
        big_card("Bot status",
                 f"{status_emoji} {status_label}",
                 "All trading + data systems checked." if status_emoji == "🟢"
                 else "Open the Engine Room page for details.",
                 value_color=status_color)

    # ── Anomaly callouts ──────────────────────────────────────────────────
    _render_anomalies()

    # ── What the bot thinks today ─────────────────────────────────────────
    section("What the bot thinks today",
            "Forecast and market price for each city the bot is actively trading.")
    _render_forecast_cards()

    # ── What we're betting today ──────────────────────────────────────────
    section("What we're betting today",
            "Live paper positions and pending offers, in plain English.")
    _render_open_positions()
    _render_pending_orders()

    # ── Why the bot skipped trades ────────────────────────────────────────
    section("Why the bot skipped trades",
            "The bot evaluates thousands of contracts a day. Most don't meet our criteria.")
    _render_skip_breakdown(days_back=1)


def _render_anomalies() -> None:
    """Show a callout when something unusual is happening. Skip silently when not."""
    callouts = []

    # 1) Bias drift — anything moved a lot in the last week?
    try:
        drift = queries.bias_drift_recent(hours=24)
        if not drift.empty:
            big_moves = drift[drift.get("abs_change", pd.Series(dtype=float)).fillna(0) > 3.0]
            if not big_moves.empty:
                row = big_moves.iloc[0]
                station = t.friendly_station(row.get("station", "?"))
                amt = float(row.get("abs_change", 0))
                callouts.append(("⚠️ Forecast bias jumped overnight",
                                 f"The model's typical error for {station} shifted by "
                                 f"{amt:.1f}°F in the last day. This can mean the "
                                 f"forecast started missing harder than usual.",
                                 "#f59e0b"))
    except Exception:
        pass  # bias_drift_event may not exist in older DBs

    # 2) Yesterday was unusually bad (>= 1.5x typical day's loss)
    y = pnl_yesterday()
    if y.get("net") is not None and y["net"] < -50:
        callouts.append(("📉 Rough day yesterday",
                         f"We lost {t.usd(abs(y['net']))} across {y['n_fills']} bets. "
                         f"Check the Trade Log to see which bets bled.",
                         "#dc2626"))

    # 3) No fills in last 24h — might mean trading is stuck
    try:
        recent = queries.signals_today()
        if not recent.empty:
            opens = recent[recent["action"] == "OPEN"]
            if len(opens) == 0:
                callouts.append(("🤔 No bets placed today",
                                 "The bot has run but hasn't opened any positions. "
                                 "This is normal if no markets met our criteria, "
                                 "but worth a glance at the Engine Room if it persists.",
                                 "#737373"))
    except Exception:
        pass

    for title, body, color in callouts:
        callout(title, body, color)


def _render_forecast_cards() -> None:
    today = datetime.now().date()
    trade_stations = queries.trade_eligible_stations()
    if not trade_stations:
        st.info("No stations currently configured for live trading.")
        return
    for station in trade_stations:
        fc = latest_forecast_for(station, today)
        mkt = market_p50_for(station, today)
        city = t.friendly_station(station)
        if fc is None:
            _md(f"""<div class="v2-row">
                    <div class="v2-row-title">{city}</div>
                    <div class="v2-row-line">No forecast available yet.</div>
                  </div>""")
            continue
        p50 = fc[50]
        p25 = fc.get(25)
        p75 = fc.get(75)
        # Plain-English range — fall back gracefully if quantiles missing
        if p25 is not None and p75 is not None:
            range_phrase = f"most likely between {p25:.0f}°F and {p75:.0f}°F"
        else:
            range_phrase = ""
        market_phrase = ""
        diff_phrase = ""
        if mkt is not None:
            market_phrase = f"<br><strong>Market price:</strong> ~{mkt:.0f}°F"
            diff = p50 - mkt
            if abs(diff) < 1.5:
                diff_phrase = "<br><strong>Disagreement:</strong> Roughly agree."
            elif diff > 0:
                diff_phrase = (f"<br><strong>Disagreement:</strong> Bot thinks "
                               f"{abs(diff):.0f}°F warmer than market.")
            else:
                diff_phrase = (f"<br><strong>Disagreement:</strong> Bot thinks "
                               f"{abs(diff):.0f}°F cooler than market.")
        # Running observed high today — pulled from latest METAR / HFMETAR.
        # CLI isn't issued until tomorrow morning so we can't use it intraday.
        running = observed_high_today(station)
        running_phrase = ""
        if running is not None:
            gap = p50 - running
            if gap > 0:
                gap_phrase = f"still {gap:.0f}°F to go to hit forecast"
            else:
                gap_phrase = f"already {abs(gap):.0f}°F past forecast"
            running_phrase = (f"<br><strong>Highest so far today:</strong> "
                              f"{running:.0f}°F &nbsp; <span style='opacity:0.75'>"
                              f"({gap_phrase})</span>")
        _md(f"""<div class="v2-row">
                <div class="v2-row-title">{city}</div>
                <div class="v2-row-line">
                  <strong>Bot expects:</strong> {p50:.0f}°F &nbsp; {range_phrase}
                  {market_phrase}
                  {running_phrase}
                  {diff_phrase}
                </div>
              </div>""")


def _render_open_positions() -> None:
    try:
        positions = queries.open_positions_with_obs()
    except Exception:
        positions = queries.open_positions()
    if positions.empty:
        _md("""<div class="v2-row">
                <div class="v2-row-line"><em>No open positions right now.</em></div>
              </div>""")
        return
    for _, p in positions.iterrows():
        city = t.friendly_station(p["station"])
        var_phrase = t.friendly_var(p["var"])
        bucket = t.bucket_phrase(p.get("lower_f"), p.get("upper_f"))
        days = int(p["days_to_settle"]) if pd.notna(p.get("days_to_settle")) else None
        when = ("today" if days == 0 else
                "tomorrow" if days == 1 else
                f"in {days} days" if days is not None else "soon")
        cost = float(p["price"]) * int(p["contracts"])
        line = (f'{side_pill(p["side"])} '
                f"<strong>{city} {var_phrase}</strong> "
                f"will be <strong>{bucket}</strong>")
        sub = (f"{int(p['contracts'])} contracts at ${float(p['price']):.2f} "
               f"= ${cost:.2f} risked &nbsp;·&nbsp; Settles {when}")
        # Live mark
        live = ""
        if pd.notna(p.get("yes_ask")) and pd.notna(p.get("yes_bid")):
            cur = float(p["yes_ask"]) if p["side"] == "YES" else (1 - float(p["yes_bid"]))
            mtm = (cur - float(p["price"])) * int(p["contracts"])
            live = (f"<br>Currently worth <strong style='color:{t.signed_color(mtm)}'>"
                    f"{t.usd(mtm, plus_sign=True)}</strong>")
        # Today's running observation if we have it
        obs_phrase = ""
        if pd.notna(p.get("obs_tmax")) and p["var"] == "TMAX_DAILY":
            obs_phrase = f"<br>So far today, observed high: {float(p['obs_tmax']):.0f}°F"
        _md(f"""<div class="v2-row">
                <div class="v2-row-line">{line}</div>
                <div class="v2-row-line" style="font-size:0.85rem; opacity:0.85;">
                  {sub}{live}{obs_phrase}
                </div>
              </div>""")


def _render_pending_orders() -> None:
    try:
        pending = queries.pending_paper_orders()
    except Exception:
        pending = pd.DataFrame()
    if pending.empty:
        return
    st.caption(f"⏳ {len(pending)} pending offer{'s' if len(pending) != 1 else ''} "
               "(orders waiting for market to fill)")
    # Compact: show count and a small table
    show = pending[["station", "side", "limit_price", "contracts",
                    "lower_f", "upper_f", "ttl_min"]].copy()
    show["city"] = show["station"].map(t.friendly_station)
    show["range"] = show.apply(lambda r: t.bucket_phrase(r["lower_f"], r["upper_f"]), axis=1)
    show["expires"] = show["ttl_min"].map(lambda x: f"{x:.0f}m" if pd.notna(x) else "—")
    show["price"] = show["limit_price"].map(lambda x: f"${x:.2f}")
    display = show[["city", "range", "side", "price", "contracts", "expires"]].rename(
        columns={"city": "City", "range": "Temperature", "side": "Bet",
                 "price": "Offering", "contracts": "Contracts", "expires": "Expires"}
    )
    st.dataframe(display, hide_index=True, use_container_width=True)


def _render_skip_breakdown(days_back: int = 1) -> None:
    try:
        df = queries.skip_breakdown(days_back=days_back)
    except Exception:
        df = pd.DataFrame()
    if df.empty:
        st.info("No skip activity recorded yet.")
        return
    total = int(df["n"].sum())
    st.caption(f"Last {days_back} day{'s' if days_back != 1 else ''} · "
               f"{total:,} contracts evaluated, most passed:")
    for _, row in df.iterrows():
        emoji, phrase = t.skip_reason_plain(row["skip_reason"])
        _md(f"""<div class="v2-skip">
                <div class="v2-skip-emoji">{emoji}</div>
                <div class="v2-skip-text">{phrase}</div>
                <div class="v2-skip-count">{int(row['n']):,} skips</div>
              </div>""")


# ---------------------------------------------------------------------------
# PAGE: TRADE LOG
# ---------------------------------------------------------------------------
def page_trade_log() -> None:
    st.title("Trade Log")
    st.caption("Every bet the bot has placed, with outcomes once they settle.")

    # ── Filters ───────────────────────────────────────────────────────────
    c1, c2 = st.columns(2)
    with c1:
        days = st.selectbox("Window", [7, 14, 30, 60, 90], index=1,
                             format_func=lambda d: f"Last {d} days")
    with c2:
        stations = sorted(queries.fetch_stations())
        station_choice = st.selectbox(
            "City", ["All"] + stations,
            format_func=lambda s: "All cities" if s == "All" else t.friendly_station(s),
        )

    df = _v2_settled_fills(days_back=int(days))
    if df.empty:
        st.info("No bets in this window.")
        return
    if station_choice != "All":
        df = df[df["station"] == station_choice]
    if df.empty:
        st.info(f"No bets for {t.friendly_station(station_choice)} in this window.")
        return

    # ── Per-city scorecard ────────────────────────────────────────────────
    settled = df[df["settled"] == True]  # noqa: E712
    if not settled.empty:
        scorecard = (
            settled.groupby("station")
                   .agg(fills=("id", "count"),
                        wins=("realized_pnl", lambda s: int((s > 0).sum())),
                        net_pnl=("realized_pnl", "sum"))
                   .reset_index()
        )
        scorecard["city"] = scorecard["station"].map(t.friendly_station)
        scorecard["win_pct"] = (scorecard["wins"] / scorecard["fills"] * 100).round(0)
        scorecard = scorecard.sort_values("net_pnl", ascending=True)
        section(f"By city · last {days} days")
        cols = st.columns(min(4, len(scorecard)))
        for i, (_, row) in enumerate(scorecard.iterrows()):
            with cols[i % len(cols)]:
                big_card(
                    row["city"],
                    t.usd(row["net_pnl"], plus_sign=True),
                    f"{int(row['wins'])}/{int(row['fills'])} won ({int(row['win_pct'])}%)",
                    value_color=t.signed_color(row["net_pnl"]),
                )

    # ── Individual trades ─────────────────────────────────────────────────
    section(f"Individual bets · last {days} days",
            "Most recent first. Click any bet to see the model details that drove it.")
    df_sorted = df.sort_values("fill_ts", ascending=False)
    for _, row in df_sorted.iterrows():
        _render_trade_row(row)


def _render_trade_row(row: pd.Series) -> None:
    city = t.friendly_station(row["station"])
    bucket = t.bucket_phrase(row.get("lower_f"), row.get("upper_f"))
    var_phrase = t.friendly_var(row["var"])
    settled = bool(row.get("settled", False))
    contracts = int(row["contracts"])
    price = float(row["price"])
    side = row["side"]

    # Outcome
    if settled:
        pnl = row.get("realized_pnl")
        if pnl is None or pd.isna(pnl):
            outcome_text = "Settled"
            outcome_color = "#737373"
        else:
            outcome_text = f"{'Won' if pnl > 0 else 'Lost'} {t.usd(abs(pnl))}"
            outcome_color = t.signed_color(pnl)
    else:
        outcome_text = "Open"
        outcome_color = "#f59e0b"

    fill_ts = pd.to_datetime(row["fill_ts"])
    fill_str = fill_ts.strftime("%b %-d, %-I:%M%p").lower()

    header = (f'{side_pill(side)} <strong>{city}</strong> '
              f'{var_phrase} will be <strong>{bucket}</strong> '
              f'on {pd.to_datetime(row["valid_date"]).strftime("%b %-d")}')
    sub = (f"{contracts} contracts at ${price:.2f} · "
           f"placed {fill_str} · "
           f"<strong style='color:{outcome_color}'>{outcome_text}</strong>")

    fair_prob = row.get("fair_prob")
    expected = row.get("expected_pnl")

    with st.expander(f"  ", expanded=False):
        # Render header/sub inside the row above; expander gets technical detail
        if fair_prob is not None and not pd.isna(fair_prob):
            side_prob = fair_prob if side == "YES" else (1 - fair_prob)
            st.write(f"**Bot's confidence:** {side_prob*100:.0f}% likely to hit this bucket")
            st.write(f"**Market price implied:** {price*100:.0f}% "
                     "(what we paid per contract, in cents)")
            if expected is not None and not pd.isna(expected):
                st.write(f"**Bot's expected P&L on this bet:** {t.usd(float(expected), plus_sign=True)}")
            if settled and not pd.isna(pnl):
                diff = float(pnl) - float(expected) if expected is not None else None
                if diff is not None:
                    if abs(diff) < 0.5:
                        st.write(f"**Reality:** Realized P&L matched expectation.")
                    elif diff > 0:
                        st.write(f"**Reality:** Did better than expected by {t.usd(diff)}.")
                    else:
                        st.write(f"**Reality:** Worse than expected by {t.usd(abs(diff))}.")
        # Inline divergence: |fair_prob − market_mid|
        if (pd.notna(row.get("fair_prob")) and pd.notna(row.get("market_ask"))
                and pd.notna(row.get("market_bid"))):
            mid = (float(row["market_ask"]) + float(row["market_bid"])) / 2.0
            div = abs(float(row["fair_prob"]) - mid)
            st.caption(f"Model–market disagreement at trade time: "
                       f"{div*100:.1f} percentage points")
        st.caption(f"Ticker: `{row['ticker']}`")

    # Now render the row content (the expander header is empty, so we put the
    # human-readable summary just above it)
    _md(f"""<div class="v2-row" style="margin-top:-0.65rem;">
            <div class="v2-row-line">{header}</div>
            <div class="v2-row-line" style="font-size:0.85rem; opacity:0.85;">{sub}</div>
          </div>""")


# ---------------------------------------------------------------------------
# PAGE: HOW IS THE BOT DOING?
# ---------------------------------------------------------------------------
def page_bot_health() -> None:
    st.title("How is the bot doing?")
    st.caption("The story of the last 30 days, in three pictures.")

    days = st.slider("Look back this many days", 14, 90, 30, step=7)

    # ── Picture 1: Cumulative P&L ─────────────────────────────────────────
    section("Are we making money over time?",
            "Cumulative profit/loss from settled bets. Anything above zero is good.")
    cum = cumulative_pnl_series(days_back=days)
    if cum.empty:
        st.info("Not enough settled trades yet.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=cum["day"], y=cum["cumulative_pnl"],
            mode="lines+markers",
            line=dict(width=3, color="#6366f1"),
            marker=dict(size=7),
            hovertemplate="<b>%{x}</b><br>Cumulative: $%{y:.2f}<extra></extra>",
        ))
        fig.add_hline(y=0, line_dash="dash", line_color="#737373", opacity=0.5)
        fig.update_layout(
            height=300,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title=None, yaxis_title="Cumulative $",
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
        latest = float(cum["cumulative_pnl"].iloc[-1])
        first = float(cum["cumulative_pnl"].iloc[0]) - float(cum["daily_pnl"].iloc[0])
        change = latest - first
        st.caption(f"Net change over window: **{t.usd(change, plus_sign=True)}** "
                   f"across {len(cum)} trading days.")

    # ── Picture 2: P&L heatmap ────────────────────────────────────────────
    section("Where is money made and lost?",
            "Rows = city, columns = bet type. Green cells are profitable; red are losing.")
    grid = pnl_cell_grid(days_back=days)
    if grid.empty:
        st.info("Not enough settled trades yet.")
    else:
        pivot = grid.pivot_table(index="city", columns="cell",
                                  values="net_pnl", aggfunc="sum").fillna(0)
        # Order columns: same-day first, then day-ahead etc.
        col_order = sorted(pivot.columns,
                            key=lambda c: (0 if "Same day" in c else 1 if "Day ahead" in c else 2,
                                           c))
        pivot = pivot[col_order]
        # Build hover text with fill counts
        counts = grid.pivot_table(index="city", columns="cell",
                                   values="fills", aggfunc="sum").fillna(0)
        counts = counts.reindex(columns=col_order, fill_value=0)
        z = pivot.values
        max_abs = max(abs(z.min()), abs(z.max()), 1)
        text = [[f"${v:.0f}<br>{int(counts.iloc[i,j])} bets"
                  for j, v in enumerate(row)]
                 for i, row in enumerate(z)]
        fig = go.Figure(data=go.Heatmap(
            z=z,
            x=list(pivot.columns),
            y=list(pivot.index),
            text=text,
            texttemplate="%{text}",
            textfont={"size": 11},
            colorscale=[[0, "#dc2626"], [0.5, "#f5f5f5"], [1, "#16a34a"]],
            zmid=0, zmin=-max_abs, zmax=max_abs,
            colorbar=dict(title="$"),
            hovertemplate="<b>%{y} · %{x}</b><br>P&L: $%{z:.2f}<extra></extra>",
        ))
        fig.update_layout(
            height=max(220, 80 + 60 * len(pivot.index)),
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_side="top",
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
        # Highlight the winning + losing cell
        best = grid.loc[grid["net_pnl"].idxmax()]
        worst = grid.loc[grid["net_pnl"].idxmin()]
        st.markdown(
            f"- 🟢 **Best cell**: {best['city']} · {best['cell']} → "
            f"{t.usd(best['net_pnl'], plus_sign=True)} ({int(best['fills'])} bets)\n"
            f"- 🔴 **Worst cell**: {worst['city']} · {worst['cell']} → "
            f"{t.usd(worst['net_pnl'], plus_sign=True)} ({int(worst['fills'])} bets)"
        )

    # ── Picture 2.5: Early exits (take-profit) ────────────────────────────
    section("How much profit does early-exit save?",
            "The bot has a take-profit rule: when an open position has captured "
            "≥85% of its maximum possible gain, sell at the current bid. This "
            "harvests winners before settlement can take them back.")
    ex = early_exit_summary(days_back=days)
    cE1, cE2, cE3 = st.columns(3)
    with cE1:
        big_card(
            "Early-exit P&L",
            t.usd(ex["exit_pnl"], plus_sign=True),
            f"{ex['exit_count']} positions closed early",
            value_color=t.signed_color(ex["exit_pnl"]),
        )
    with cE2:
        big_card(
            "Held-to-settlement P&L",
            t.usd(ex["settled_pnl"], plus_sign=True),
            f"{ex['settled_count']} positions held",
            value_color=t.signed_color(ex["settled_pnl"]),
        )
    with cE3:
        # Share of total P&L coming from early exits — caps at 100% display
        if ex["exit_count"] == 0:
            big_card("Take-profit hits", "0 fires", "No early exits in this window.")
        else:
            share = ex["exit_share_of_pnl"]
            sub = (f"Early-exit accounts for {share:+.0f}% of net P&L."
                   if abs(share) < 200 else
                   "Early-exit dominates the P&L story.")
            big_card("Take-profit firing rate",
                     f"{ex['exit_count']} fires",
                     sub)
    if ex["exit_count"] > 0 and ex["exit_pnl"] > 0 and ex["settled_pnl"] < 0:
        callout(
            "Early-exit is doing heavy lifting",
            f"In this window, early exits saved <strong>{t.usd(ex['exit_pnl'])}</strong> "
            f"while held-to-settlement positions lost <strong>{t.usd(abs(ex['settled_pnl']))}</strong>. "
            "Without the take-profit rule, the bot's net would be substantially worse.",
            color="#16a34a",
        )

    # ── Picture 3: Calibration ────────────────────────────────────────────
    section("Is the bot well-calibrated?",
            "When the bot says it's X% confident, the bet should win X% of the time. "
            "Dots above the diagonal mean the bot is underconfident; below means overconfident.")
    try:
        cal = queries.bucket_calibration(days_back=days, n_bins=10)
    except Exception:
        cal = pd.DataFrame()
    if cal.empty:
        st.info("Not enough settled trades for calibration yet.")
    else:
        fig = go.Figure()
        # Perfect-calibration diagonal
        fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines",
                                  line=dict(dash="dash", color="#737373", width=1),
                                  showlegend=False, hoverinfo="skip"))
        sizes = (cal["n"].astype(float).clip(lower=5)).tolist()
        max_size = max(sizes) if sizes else 1
        sizes = [10 + 30 * (s / max_size) for s in sizes]
        fig.add_trace(go.Scatter(
            x=cal["mean_pred"], y=cal["observed_freq"],
            mode="markers",
            marker=dict(size=sizes, color="#6366f1", line=dict(color="white", width=1)),
            hovertemplate=("Confidence ~%{x:.0%}<br>"
                           "Actual win rate: %{y:.0%}<br>"
                           "%{customdata} bets<extra></extra>"),
            customdata=cal["n"],
            showlegend=False,
        ))
        fig.update_layout(
            height=340,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(title="Bot's confidence", range=[0, 1], tickformat=".0%"),
            yaxis=dict(title="Actual win rate", range=[0, 1], tickformat=".0%"),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
        # Verbal summary
        cal_sorted = cal.sort_values("n", ascending=False)
        top = cal_sorted.iloc[0]
        diff = float(top["observed_freq"] - top["mean_pred"])
        bias_word = ("close to calibrated"
                     if abs(diff) < 0.05
                     else ("underconfident" if diff > 0 else "overconfident"))
        st.caption(f"At the bot's most common confidence level "
                   f"(~{float(top['mean_pred'])*100:.0f}%), "
                   f"it wins {float(top['observed_freq'])*100:.0f}% of the time. "
                   f"That's **{bias_word}**.")


# ---------------------------------------------------------------------------
# PAGE: NEW CITIES
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30)
def _new_cities_overview() -> pd.DataFrame:
    """Per fetch-only city: data coverage + current forecast + observed-so-far.

    Used by the New cities page to show what we know about each station the
    bot is collecting on but not yet trading.
    """
    from weather_bot.config import ACTIVE_FETCH_STATIONS, ACTIVE_TRADE_STATIONS
    fetch_only = [s for s in ACTIVE_FETCH_STATIONS if s not in ACTIVE_TRADE_STATIONS]
    if not fetch_only:
        return pd.DataFrame()

    sql = """
    WITH targets AS (
        SELECT UNNEST(%s::text[]) AS code
    ),
    metar AS (
        SELECT m.station,
               COUNT(*) AS n_24h,
               MAX(CASE
                     WHEN (m.obs_time AT TIME ZONE st.tz)::date = CURRENT_DATE
                       THEN m.temp_f
                   END) AS running_high_today
          FROM metar_obs m
          JOIN stations st ON st.code = m.station
         WHERE m.obs_time >= NOW() - INTERVAL '24 hours'
         GROUP BY m.station
    ),
    nbm_latest AS (
        SELECT pf.station, pf.value AS p50_today
          FROM prob_forecast pf
          JOIN (
              SELECT station, MAX(run_time) AS rt
                FROM prob_forecast
               WHERE valid_date = CURRENT_DATE
                 AND var = 'TMAX_DAILY'
               GROUP BY station
          ) lr ON lr.station = pf.station AND lr.rt = pf.run_time
         WHERE pf.valid_date = CURRENT_DATE
           AND pf.var = 'TMAX_DAILY'
           AND pf.percentile = 50
    ),
    bias_rows AS (
        SELECT station,
               COUNT(*) AS bias_cells,
               COUNT(*) FILTER (WHERE sample_size >= 10) AS bias_cells_thick,
               -- BIAS_GATE eligibility for live trading: a thick cell for
               -- the CURRENT month at lead_day=0 (same-day, the only cell
               -- with proven edge per our PnL audit).
               MAX(sample_size) FILTER (
                   WHERE month = EXTRACT(MONTH FROM CURRENT_DATE)::int
                     AND lead_day = 0
               ) AS this_month_lead0_n,
               MAX(updated_at) AS last_bias_update
          FROM station_bias
         WHERE cycle_hour = -1 AND var = 'TMAX_DAILY'
         GROUP BY station
    ),
    kalshi AS (
        SELECT station, COUNT(*) AS markets_today
          FROM kalshi_market
         WHERE valid_date = CURRENT_DATE
         GROUP BY station
    )
    SELECT t.code AS station,
           COALESCE(m.n_24h, 0)::int AS metar_24h,
           m.running_high_today,
           ROUND(n.p50_today::numeric, 0) AS nbm_p50_today,
           COALESCE(b.bias_cells, 0)::int AS bias_cells,
           COALESCE(b.bias_cells_thick, 0)::int AS bias_cells_thick,
           COALESCE(b.this_month_lead0_n, 0)::int AS this_month_lead0_n,
           b.last_bias_update,
           COALESCE(k.markets_today, 0)::int AS kalshi_markets_today
      FROM targets t
      LEFT JOIN metar m ON m.station = t.code
      LEFT JOIN nbm_latest n ON n.station = t.code
      LEFT JOIN bias_rows b ON b.station = t.code
      LEFT JOIN kalshi k ON k.station = t.code
     ORDER BY t.code
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (fetch_only,))
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def page_new_cities() -> None:
    st.title("New cities")
    st.caption(
        "The bot ingests weather data for 14 cities beyond the 3 it actively trades. "
        "Each one needs ~30 days of bias data before its trading gate (n≥10) opens. "
        "Use this page to confirm the pipeline is healthy and watch the bias tables fill in."
    )

    df = _new_cities_overview()
    if df.empty:
        st.info("No fetch-only cities configured.")
        return

    # ── Top-level summary ────────────────────────────────────────────────
    total = len(df)
    metar_ok = int((df["metar_24h"] > 50).sum())
    nbm_ok = int(df["nbm_p50_today"].notna().sum())
    # Eligibility for trading = current month + lead_day=0 cell at n≥10.
    # This is what is_station_calibrated checks before letting any signal OPEN.
    graduation_ready = df[df["this_month_lead0_n"] >= 10]
    n_ready = len(graduation_ready)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        big_card("Cities", str(total), "fetch-only")
    with c2:
        big_card("METAR healthy", f"{metar_ok}/{total}",
                 ">50 obs in last 24h")
    with c3:
        big_card("NBM forecast", f"{nbm_ok}/{total}",
                 "today's p50 available")
    with c4:
        big_card(
            "Graduation-ready",
            f"{n_ready}/{total}",
            "this month, lead=0, n≥10",
            value_color="#16a34a" if n_ready > 0 else None,
        )

    # ── Graduation candidates callout ────────────────────────────────────
    if n_ready > 0:
        city_list = ", ".join(
            t.friendly_station(s) for s in sorted(graduation_ready["station"])
        )
        callout(
            f"🎓 {n_ready} cit{'ies' if n_ready != 1 else 'y'} ready to graduate",
            f"<strong>{city_list}</strong> now ha{'ve' if n_ready != 1 else 's'} "
            "a thick same-day bias cell for the current month. To promote, "
            "edit <code>ACTIVE_TRADE_STATIONS</code> in <code>config.py</code> "
            "and commit — auto-deploy will activate trading on the next "
            "main.py tick. BIAS_GATE will continue to block longer leads "
            "until those cells thicken too.",
            color="#16a34a",
        )
    else:
        callout(
            "No graduation-ready cities yet",
            "None of the fetch-only cities have a thick same-day bias cell "
            "for the current month. This is expected within the first 2-3 "
            "weeks after a city is added — the bias retrain needs paired "
            "forecast+observation history to compute usable cells. "
            "See <em>Per-city status</em> below for each city's progress.",
            color="#737373",
        )

    # ── Per-city detail ──────────────────────────────────────────────────
    section("Per-city status")
    display = df.copy()
    display["city"] = display["station"].map(t.friendly_station)
    display["running_high_today"] = display["running_high_today"].map(
        lambda x: f"{x:.0f}°F" if pd.notna(x) else "—"
    )
    display["nbm_p50_today"] = display["nbm_p50_today"].map(
        lambda x: f"{x:.0f}°F" if pd.notna(x) else "—"
    )
    display["last_bias_update"] = display["last_bias_update"].map(
        lambda x: pd.Timestamp(x).strftime("%Y-%m-%d") if pd.notna(x) else "—"
    )
    display = display[[
        "city", "station", "kalshi_markets_today",
        "metar_24h", "nbm_p50_today", "running_high_today",
        "bias_cells", "bias_cells_thick", "this_month_lead0_n",
        "last_bias_update",
    ]].rename(columns={
        "city": "City",
        "station": "Station",
        "kalshi_markets_today": "Markets today",
        "metar_24h": "METAR (24h)",
        "nbm_p50_today": "NBM p50 today",
        "running_high_today": "Observed so far",
        "bias_cells": "Bias cells",
        "bias_cells_thick": "Cells n≥10",
        "this_month_lead0_n": "This-mo lead=0 n",
        "last_bias_update": "Bias last updated",
    })
    st.dataframe(display, hide_index=True, use_container_width=True)

    st.caption(
        "**Bias cells** = number of (month, lead_day) combinations the bias retraining "
        "has been able to compute for this station. **Cells n≥10** = how many of those "
        "are sample-thick enough to pass BIAS_GATE. When this count is positive for "
        "the current month's lead_day=0, the station is technically eligible to trade — "
        "but you'd still want a few more days of data before promoting it to ACTIVE_TRADE_STATIONS."
    )


# ---------------------------------------------------------------------------
# PAGE: ENGINE ROOM
# ---------------------------------------------------------------------------
def page_engine_room() -> None:
    st.title("Engine Room")
    st.caption("Technical diagnostics. For full detail, run the original dashboard "
               "(`streamlit run dashboard/app.py --server.port 8501`) — it has every "
               "model internals, raw bias tables, replay harness, and so on. "
               "This page exposes the most useful pieces inline.")

    tabs = st.tabs(["Component health", "Skip reasons (raw)",
                     "Bias table", "Model accuracy", "Open positions (raw)"])

    with tabs[0]:
        st.subheader("Latest health-check status per component")
        try:
            health = queries.latest_health()
        except Exception as e:
            st.error(f"Could not load health: {e}")
            return
        if health.empty:
            st.info("No health-check rows in the database yet.")
        else:
            # Pick whichever optional columns exist on this DB
            wanted = ["station", "component", "status", "ts",
                      "metric_value", "detail", "message"]
            available = [c for c in wanted if c in health.columns]
            show = health[available].copy()
            if "ts" in show.columns:
                show["ts"] = pd.to_datetime(show["ts"]).dt.strftime("%Y-%m-%d %H:%M")
            st.dataframe(show, hide_index=True, use_container_width=True)

    with tabs[1]:
        st.subheader("All skip reasons, raw counts")
        days = st.slider("Days back", 1, 30, 7, key="er_skip_days")
        try:
            df = queries.skip_breakdown(days_back=days)
            if df.empty:
                st.info("No skips in this window.")
            else:
                df_show = df.copy()
                df_show["plain_english"] = df_show["skip_reason"].map(
                    lambda c: t.skip_reason_plain(c)[1]
                )
                st.dataframe(
                    df_show[["skip_reason", "plain_english", "n", "n_tickers"]]
                        .rename(columns={"n": "Count",
                                          "n_tickers": "Distinct tickers"}),
                    hide_index=True, use_container_width=True,
                )
        except Exception as e:
            st.error(f"Query failed: {e}")

    with tabs[2]:
        st.subheader("Station bias table")
        st.caption("The model's learned per-(station, month, lead_day) bias and "
                   "stddev. Higher sample_size = more trustworthy. "
                   "The pre-trade BIAS_GATE refuses to trade cells with n < 10.")
        try:
            bias = queries.bias_table_summary()
            if bias.empty:
                st.info("Bias table is empty — has the retrain job run?")
            else:
                col1, col2 = st.columns(2)
                with col1:
                    sta_filter = st.selectbox(
                        "Station",
                        ["All"] + sorted(bias["station"].unique()),
                        key="er_bias_station",
                    )
                with col2:
                    var_filter = st.selectbox(
                        "Variable",
                        ["All"] + sorted(bias["var"].unique()),
                        key="er_bias_var",
                    )
                view = bias.copy()
                if sta_filter != "All":
                    view = view[view["station"] == sta_filter]
                if var_filter != "All":
                    view = view[view["var"] == var_filter]
                st.dataframe(view, hide_index=True, use_container_width=True,
                              height=400)
        except Exception as e:
            st.error(f"Query failed: {e}")

    with tabs[3]:
        st.subheader("Forecast model accuracy vs CLI ground truth")
        st.caption("Lower MAE = better. NBM is our primary model; HRRR/GFS/ECMWF "
                   "are diagnostic comparisons.")
        days = st.slider("Days back", 7, 60, 30, key="er_acc_days")
        try:
            df = queries.model_accuracy(days_back=days)
            if df.empty:
                st.info("No comparable observations in this window.")
            else:
                summary = (df.groupby(["station", "model"], as_index=False)
                             .agg(n=("abs_err", "count"),
                                  mae=("abs_err", "mean"),
                                  bias=("err", "mean") if "err" in df.columns
                                       else ("abs_err", "mean")))
                summary["mae"] = summary["mae"].round(2)
                if "bias" in summary.columns:
                    summary["bias"] = summary["bias"].round(2)
                st.dataframe(summary, hide_index=True, use_container_width=True)
        except Exception as e:
            st.error(f"Query failed: {e}")

    with tabs[4]:
        st.subheader("Open positions (raw)")
        try:
            positions = queries.open_positions_with_obs()
            if positions.empty:
                st.info("No open positions.")
            else:
                st.dataframe(positions, hide_index=True, use_container_width=True)
        except Exception as e:
            st.error(f"Query failed: {e}")

    st.divider()
    st.caption("Want the full dashboard? In another terminal, run: "
               "`streamlit run dashboard/app.py --server.port 8501` "
               "and open it side-by-side.")


# ---------------------------------------------------------------------------
# Sidebar + dispatch
# ---------------------------------------------------------------------------
PAGES = {
    "Today": page_today,
    "Trade Log": page_trade_log,
    "How is the bot doing?": page_bot_health,
    "New cities": page_new_cities,
    "Engine Room": page_engine_room,
}

with st.sidebar:
    st.title("weather_bot")
    st.caption("Dashboard · v2")
    selected = st.radio("Page", list(PAGES.keys()), key="v2_page",
                         label_visibility="collapsed")
    st.divider()
    auto = st.toggle("Auto-refresh every 15s", value=True,
                      help="Disable when you want to keep your scroll position.")
    if auto:
        st_autorefresh(interval=15_000, key="v2_refresh")
    if st.button("Refresh now", use_container_width=True):
        queries.clear_cache()
        st.rerun()
    st.caption(f"Loaded at {datetime.now().strftime('%H:%M:%S')}")
    st.divider()
    st.caption("**Trading live:** " + ", ".join(
        t.friendly_station(s) for s in queries.trade_eligible_stations()))
    fetch_only = [s for s in queries.fetch_stations()
                   if s not in queries.trade_eligible_stations()]
    if fetch_only:
        st.caption("**Collecting data:** " + ", ".join(
            t.friendly_station(s) for s in fetch_only))

# Dispatch
PAGES[selected]()
