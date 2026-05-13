"""Streamlit command center for weather_bot.

Run:
    streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501

Auto-refreshes every 15 seconds. Reads directly from Postgres — no service
layer between the DB and the UI by design.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from weather_bot import config
from weather_bot.dashboard import help_text, queries, replay as replay_engine
from weather_bot.data import persistence

st.set_page_config(page_title="weather_bot · Command Center", layout="wide", page_icon="🌡️")

# Tab dispatch lives in the sidebar (st.radio) instead of st.tabs because
# tabs eagerly evaluate every panel on every refresh — visible cost on a
# 15s auto-refresh. Radio renders only the selected tab.
TAB_ORDER = ["Simple", "Home", "Trading", "Profitability", "Status", "Calibration", "Deep Dive"]

st.markdown("""
<style>
  .block-container { padding-top: 1.2rem; }
  .wb-card {
    border: 1px solid rgba(128, 128, 128, 0.25);
    border-radius: 8px;
    padding: 0.85rem 1rem;
    background: rgba(128, 128, 128, 0.05);
    min-height: 102px;
  }
  .wb-label {
    font-size: 0.75rem;
    text-transform: uppercase;
    color: rgba(128, 128, 128, 0.95);
    letter-spacing: 0;
  }
  .wb-value { font-size: 1.55rem; font-weight: 700; margin-top: 0.2rem; }
  .wb-sub { font-size: 0.84rem; color: rgba(128, 128, 128, 0.95); margin-top: 0.2rem; }
  .wb-callout {
    border-left: 6px solid var(--wb-color);
    border-radius: 8px;
    padding: 1rem 1.1rem;
    background: rgba(128, 128, 128, 0.06);
    margin: 0.4rem 0 1rem 0;
  }
  .wb-callout-title { font-size: 1.3rem; font-weight: 700; margin-bottom: 0.25rem; }
  .wb-callout-body { font-size: 0.98rem; line-height: 1.45; color: rgba(128, 128, 128, 0.98); }
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.title("Controls")
    selected_tab = st.radio("View", TAB_ORDER, key="selected_tab",
                              label_visibility="collapsed")
    st.divider()
    auto_refresh = st.toggle("Auto-refresh (15s)", value=True,
                              help="Disable when investigating to keep your scroll position.")
    if auto_refresh:
        st_autorefresh(interval=15_000, key="auto_refresh")
    if st.button("🔄 Refresh now", use_container_width=True,
                 help="Clear the dashboard's 12s query cache and re-pull immediately."):
        queries.clear_cache()
        st.rerun()
    st.caption("Last loaded: " + datetime.now().strftime("%H:%M:%S"))
    st.divider()
    show_help = st.toggle("Show help panels", value=False,
                          help="Expand legend / explanation text on each tab.")
    st.divider()
    st.caption("**Trading:** " + ", ".join(queries.trade_eligible_stations()))
    st.caption("**Gathering data only:** " + ", ".join(s for s in queries.fetch_stations()
                                              if s not in queries.trade_eligible_stations()))
    st.divider()
    with st.expander("Glossary"):
        st.markdown(help_text.GLOSSARY)


# ---------------------------------------------------------------------------
# Visual helpers
# ---------------------------------------------------------------------------
COLOR = {"GREEN": "#16a34a", "AMBER": "#f59e0b", "RED": "#dc2626", "GREY": "#737373"}
EMOJI = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴"}


def _bucket_label(lower_f, upper_f) -> str:
    """Human-readable Kalshi temperature bucket matching Kalshi's exact display text.

    upper_f is stored as exclusive (hi+1) by the parser, so we subtract 1
    before displaying. Format mirrors Kalshi: "79° to 80°", "78° or below", "87° or above".
    """
    lo = lower_f if lower_f is not None and not (isinstance(lower_f, float) and pd.isna(lower_f)) else None
    hi = upper_f if upper_f is not None and not (isinstance(upper_f, float) and pd.isna(upper_f)) else None
    if lo is None and hi is None:
        return "?"
    if lo is None:
        return f"{hi - 1:.0f}° or below"
    if hi is None:
        return f"{lo:.0f}° or above"
    return f"{lo:.0f}° to {hi - 1:.0f}°"


def status_pill(label: str, status: str, value: str | None = None, sub: str | None = None,
                tooltip: str | None = None):
    color = COLOR.get(status, COLOR["GREY"])
    emoji = EMOJI.get(status, "⚪")
    sub_html = f"<div style='font-size:0.85em;opacity:0.85'>{sub}</div>" if sub else ""
    val_html = f"<div style='font-size:1.4em;font-weight:600;margin-top:4px'>{value}</div>" if value else ""
    block = f"""
        <div style="background:{color};color:white;padding:14px 18px;border-radius:8px;margin-bottom:8px;">
          <div style='font-size:0.78em;letter-spacing:0.05em;text-transform:uppercase'>{label} {emoji}</div>
          {val_html}
          {sub_html}
        </div>
        """
    st.markdown(block, unsafe_allow_html=True)
    if tooltip:
        st.caption(tooltip)


def metric_card(label: str, value: str, sub: str = "", color: str | None = None):
    style = f"color:{color};" if color else ""
    st.markdown(
        f"""
        <div class="wb-card">
          <div class="wb-label">{label}</div>
          <div class="wb-value" style="{style}">{value}</div>
          <div class="wb-sub">{sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def callout(title: str, body: str, color: str = "#16a34a"):
    st.markdown(
        f"""
        <div class="wb-callout" style="--wb-color:{color};">
          <div class="wb-callout-title">{title}</div>
          <div class="wb-callout-body">{body}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def overall_status(rows: pd.DataFrame, components: list[str], stations_filter: set[str] | None = None) -> str:
    if rows.empty: return "GREY"
    sub = rows[rows["component"].str.startswith(tuple(components))]
    if stations_filter is not None:
        sub = sub[sub["station"].isin(stations_filter | {"GLOBAL"})]
    if sub.empty: return "GREY"
    if (sub["status"] == "RED").any(): return "RED"
    if (sub["status"] == "AMBER").any(): return "AMBER"
    return "GREEN"


SKIP_REASON_LABELS = {
    "BIAS_GATE": "waiting for enough station history",
    "TRIPWIRE_RED": "safety stop is active",
    "NO_EDGE": "market price is not good enough",
    "DIVERGENCE": "bot and market disagree too much",
    "PROFIT_GATE": "profitability guardrail blocked it",
    "FEE_LOAD": "fees are too high",
    "NO_BOOK": "missing usable market price",
    "AGREEMENT": "weather models do not agree",
    "UNCLASSIFIED": "uncategorized skip",
}


def _model_trust_summary() -> tuple[str, str, str]:
    rel = queries.event_reliability_bins(days_back=30)
    if rel.empty:
        return "Learning", "not enough settled signal outcomes yet", COLOR["GREY"]
    rel = rel.copy()
    rel["gap"] = (rel["mean_pred"].astype(float) - rel["observed_freq"].astype(float)).abs()
    rel["weight"] = rel["n_events"].astype(float).clip(lower=0.0)
    total_weight = float(rel["weight"].sum())
    if total_weight <= 0:
        return "Learning", "not enough effective events yet", COLOR["GREY"]
    weighted_gap = float((rel["gap"] * rel["weight"]).sum() / total_weight)
    if weighted_gap >= 0.15:
        return "Low", f"average miss is about {weighted_gap:.0%}", COLOR["RED"]
    if weighted_gap >= 0.08:
        return "Mixed", f"average miss is about {weighted_gap:.0%}", COLOR["AMBER"]
    return "Good", f"average miss is about {weighted_gap:.0%}", COLOR["GREEN"]


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
def tab_simple():
    health = queries.latest_health()
    positions = queries.open_positions_with_obs()
    signals = queries.signals_today()
    skip = queries.skip_breakdown(days_back=7)
    today = queries.pnl_today()
    yest = queries.pnl_yesterday()
    slices = queries.profitability_slices(days_back=7)

    red_alerts = 0
    amber_alerts = 0
    if not health.empty:
        red_alerts = len(health[(health.status == "RED") & (health.acknowledged_at.isna())])
        amber_alerts = len(health[health.status == "AMBER"])

    opens_today = int((signals["action"] == "OPEN").sum()) if not signals.empty else 0
    skips_today = int((signals["action"] == "SKIP").sum()) if not signals.empty else 0
    open_cost = 0.0
    if not positions.empty:
        open_cost = float((positions["price"].astype(float) * positions["contracts"].astype(int)).sum())

    trust_label, trust_detail, trust_color = _model_trust_summary()
    pnl_7d = 0.0 if slices.empty else float(slices["net_pnl"].sum())

    if red_alerts:
        verdict = "Stop and look"
        verdict_body = f"{red_alerts} red safety alert is active. The bot should be blocking affected new trades until reviewed."
        verdict_color = COLOR["RED"]
    elif trust_label == "Low":
        verdict = "Paper only"
        verdict_body = "The model is still too overconfident. Let it keep collecting data, but do not treat the probabilities as live-money ready."
        verdict_color = COLOR["AMBER"]
    elif amber_alerts:
        verdict = "Watch"
        verdict_body = f"{amber_alerts} warning item is active. Nothing screams broken, but the bot deserves a quick glance."
        verdict_color = COLOR["AMBER"]
    else:
        verdict = "Looks okay"
        verdict_body = "Feeds and safety checks look normal. Still paper trading, so this is observation mode."
        verdict_color = COLOR["GREEN"]

    st.subheader("Simple View", help="Plain-English summary first; detailed diagnostics stay in the other tabs.")
    callout(verdict, verdict_body, verdict_color)

    cols = st.columns(4)
    with cols[0]:
        metric_card("Mode", "Paper only" if config.PAPER_MODE else "Live trading",
                    "no real orders" if config.PAPER_MODE else "real orders enabled",
                    COLOR["GREEN"] if config.PAPER_MODE else COLOR["RED"])
    with cols[1]:
        trade_text = f"{opens_today} opened" if opens_today else "No new trades"
        metric_card("Today", trade_text, f"{skips_today} skipped")
    with cols[2]:
        metric_card("Open Risk", f"${open_cost:,.0f}", f"{len(positions)} open paper positions")
    with cols[3]:
        metric_card("Model Trust", trust_label, trust_detail, trust_color)

    st.divider()

    left, right = st.columns([1, 1])
    with left:
        st.subheader("What The Bot Is Doing")
        if signals.empty:
            st.info("No signals have been logged today.")
        elif opens_today == 0:
            if not skip.empty:
                top = skip.iloc[0]
                reason = str(top["skip_reason"])
                simple_reason = SKIP_REASON_LABELS.get(reason, reason.lower())
                callout(
                    "Mostly waiting",
                    f"The main reason is: <strong>{simple_reason}</strong>. Raw code: <code>{reason}</code>.",
                    COLOR["AMBER"],
                )
            else:
                callout("Mostly waiting", "The bot has not found a trade worth opening today.", COLOR["AMBER"])
        else:
            callout("Taking paper trades", f"The bot opened {opens_today} paper trade(s) today.", COLOR["GREEN"])

        if not skip.empty:
            easy_skip = skip.head(5).copy()
            easy_skip["plain_english"] = easy_skip["skip_reason"].map(SKIP_REASON_LABELS).fillna(easy_skip["skip_reason"])
            st.dataframe(
                easy_skip[["plain_english", "n", "n_tickers"]],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "plain_english": "Why it skipped",
                    "n": "Signals",
                    "n_tickers": "Markets",
                },
            )

    with right:
        st.subheader("Money Snapshot")
        money_cols = st.columns(3)
        money_cols[0].metric("Today", f"${today['net']:+,.2f}")
        money_cols[1].metric("Last 7 days", f"${pnl_7d:+,.2f}")
        if yest["net"] is None:
            money_cols[2].metric("Yesterday", "n/a")
        else:
            money_cols[2].metric("Yesterday", f"${yest['net']:+,.2f}", f"{yest['n_wins']}/{yest['n_fills']} wins")
        st.caption("These are paper-trading dollars. Negative numbers are useful feedback while calibration is being fixed.")

    st.divider()
    st.subheader("Open Positions")
    if positions.empty:
        st.info("No open paper positions.")
    else:
        simple_pos = positions.copy()
        simple_pos["bucket"] = simple_pos.apply(lambda r: _bucket_label(r.get("lower_f"), r.get("upper_f")), axis=1)
        simple_pos["cost"] = simple_pos["price"].astype(float) * simple_pos["contracts"].astype(int)
        simple_pos["day"] = simple_pos["valid_date"].astype(str)
        st.dataframe(
            simple_pos[["station", "day", "bucket", "side", "contracts", "cost"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "station": "City",
                "day": "Weather day",
                "bucket": "Temperature range",
                "side": "Bet",
                "contracts": "Contracts",
                "cost": st.column_config.NumberColumn("Cost", format="$%.2f"),
            },
        )

    st.divider()
    with st.expander("Advanced detail charts"):
        tab_home()


def tab_home():
    health = queries.latest_health()
    trade_stations = set(queries.trade_eligible_stations())
    today = queries.pnl_today()
    yest = queries.pnl_yesterday()
    positions = queries.open_positions_with_obs()
    signals = queries.signals_today()
    skip = queries.skip_breakdown(days_back=7)

    red_alerts = 0
    if not health.empty:
        red_alerts = len(health[(health.status == "RED") & (health.acknowledged_at.isna())])

    st.subheader("Today", help="The shortest path to: are we healthy, exposed, and making money?")
    cols = st.columns(5)
    with cols[0]:
        color = "#16a34a" if today["net"] >= 0 else "#dc2626"
        metric_card("Today P&L", f"${today['net']:+,.2f}",
                    f"${today['realized']:+.2f} settled · ${today['unrealized']:+.2f} MtM", color)
    with cols[1]:
        metric_card("Open Positions", str(len(positions)), f"{today['n_open']} for today's valid date")
    with cols[2]:
        opens = int((signals["action"] == "OPEN").sum()) if not signals.empty else 0
        skips = int((signals["action"] == "SKIP").sum()) if not signals.empty else 0
        metric_card("Signals Today", f"{opens} open", f"{skips} skipped")
    with cols[3]:
        y_sub = "no settled fills yesterday" if yest["net"] is None else f"{yest['n_wins']}/{yest['n_fills']} wins"
        y_val = "n/a" if yest["net"] is None else f"${yest['net']:+,.2f}"
        metric_card("Yesterday", y_val, y_sub)
    with cols[4]:
        alert_color = "#dc2626" if red_alerts else "#16a34a"
        metric_card("Blocking Alerts", str(red_alerts), "unacked red health rows", alert_color)

    st.divider()

    left, right = st.columns([1.25, 1])
    with left:
        st.subheader("Open Position Watchlist", help="Settlement state and mark-to-market for live paper positions.")
        if positions.empty:
            st.info("No open positions.")
        else:
            watch = positions.copy()
            states = watch.apply(lambda r: pd.Series(_settlement_state(r),
                                                     index=["state", "_state_color", "_state_explain"]),
                                 axis=1)
            watch = pd.concat([watch, states], axis=1)
            watch["bucket"] = watch.apply(lambda r: _bucket_label(r.get("lower_f"), r.get("upper_f")), axis=1)
            watch["mtm"] = watch.apply(
                lambda r: None if pd.isna(r.yes_ask) or pd.isna(r.yes_bid)
                else ((float(r.yes_ask) if r.side == "YES" else 1.0 - float(r.yes_bid)) - float(r.price))
                * int(r.contracts),
                axis=1,
            )
            st.dataframe(
                watch[["station", "bucket", "side", "price", "contracts", "valid_date",
                       "days_to_settle", "p50", "mtm", "state", "ticker"]],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "price": st.column_config.NumberColumn(format="%.2f"),
                    "p50": st.column_config.NumberColumn(format="%.1f"),
                    "mtm": st.column_config.NumberColumn(format="$%+.2f"),
                },
            )

    with right:
        st.subheader("Why Trades Are Skipped", help="Last 7 days, grouped by canonical skip reason.")
        if skip.empty:
            st.caption("No skip signals in the last 7 days.")
        else:
            fig = px.bar(skip, x="skip_reason", y="n", text="n",
                         labels={"skip_reason": "Reason", "n": "Signals"},
                         color="skip_reason")
            fig.update_layout(height=320, showlegend=False, margin=dict(l=5, r=5, t=5, b=5))
            st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("Station Snapshot", help="The current weather context for each trade station.")
    cols = st.columns(len(queries.trade_eligible_stations()))
    for col, st_code in zip(cols, queries.trade_eligible_stations()):
        with col:
            rate = queries.temp_rate_of_change(st_code)
            field = queries.regional_temp_field(st_code)
            primary_temp = field["primary_temp"] if field else (rate["last_temp_f"] if rate else None)
            rate_str = ""
            if rate is not None and rate.get("rate_f_per_hr") is not None:
                direction = "warming" if rate["rate_f_per_hr"] > 0.1 else ("cooling" if rate["rate_f_per_hr"] < -0.1 else "flat")
                rate_str = f"{direction} {abs(rate['rate_f_per_hr']):.1f} deg/hr"
            elif rate is not None and rate.get("suppressed"):
                rate_str = "rate suppressed"
            temp = "no recent temp" if primary_temp is None else f"{primary_temp:.1f}F"
            if field is not None and field["n_stations"] > 1:
                sub = f"{rate_str} · spread {field['spread']:.1f}F · primary {field['vs_mean']:+.1f}F vs mean"
            else:
                sub = rate_str or "waiting on regional field"
            metric_card(st_code, temp, sub)


def tab_profitability():
    st.subheader("Profitability Guardrails", help="Production controls currently shaping paper/live entries.")
    guard_cols = st.columns(5)
    with guard_cols[0]:
        metric_card("Controls", "on" if config.PROFIT_CONTROLS_ENABLED else "off",
                    "PROFIT_CONTROLS_ENABLED")
    with guard_cols[1]:
        paused = ", ".join(config.PAUSED_TRADE_STATIONS) or "none"
        metric_card("Paused Stations", paused, "skip reason PROFIT_GATE")
    with guard_cols[2]:
        metric_card("KNYC Lead 1+", f"{config.KNYC_L1_SIZE_MULT:.0%}", "size multiplier")
    with guard_cols[3]:
        metric_card("NO Under 50c", f"{config.NO_UNDER_50C_SIZE_MULT:.0%}", "size multiplier")
    with guard_cols[4]:
        metric_card("YES 25-50c", f"{config.YES_25_50C_SIZE_MULT:.0%}", "size multiplier")

    st.divider()
    days_back = st.slider("Profitability window", min_value=7, max_value=90, value=30, step=7)
    slices = queries.profitability_slices(days_back=days_back)

    if slices.empty:
        st.info("No settled fills in this window yet.")
    else:
        total_pnl = float(slices["net_pnl"].sum())
        total_expected = float(slices["model_claimed_ev"].sum())
        fills = int(slices["fills"].sum())
        metric_cols = st.columns(4)
        metric_cols[0].metric("Net P&L", f"${total_pnl:+,.2f}")
        metric_cols[1].metric("Model-Claimed EV", f"${total_expected:+,.2f}")
        metric_cols[2].metric("Actual vs Claimed", f"${total_pnl - total_expected:+,.2f}")
        metric_cols[3].metric("Settled Fills", f"{fills:,}")
        st.caption(
            "Model-Claimed EV is what the bot believed at entry. A large positive "
            "claim with weak realized P&L is an overconfidence warning, not profit."
        )

        chart = slices.copy()
        chart["slice"] = (
            chart["station"] + " L" + chart["lead_day"].astype(str) + " " +
            chart["side"] + " " + chart["price_band"]
        )
        fig = px.bar(chart.sort_values("net_pnl"), x="net_pnl", y="slice", orientation="h",
                     color="net_pnl", color_continuous_scale=["#dc2626", "#d4d4d4", "#16a34a"],
                     labels={"net_pnl": "Net P&L", "slice": "Slice"})
        fig.update_layout(height=max(360, 26 * len(chart)), margin=dict(l=5, r=5, t=5, b=5))
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(
            slices,
            use_container_width=True,
            hide_index=True,
            column_config={
                "avg_price": st.column_config.NumberColumn(format="%.2f"),
                "win_rate": st.column_config.NumberColumn(format="%.1%"),
                "net_pnl": st.column_config.NumberColumn(format="$%+.2f"),
                "pnl_per_fill": st.column_config.NumberColumn(format="$%+.2f"),
                "model_claimed_ev": st.column_config.NumberColumn(format="$%+.2f"),
                "realized_vs_claimed": st.column_config.NumberColumn(format="$%+.2f"),
            },
        )

    st.divider()
    st.subheader("Latest Profitability Replay", help="Output from jobs/profitability_report.py.")
    reports = sorted(Path("research/reports").glob("profitability_report_*.md"))
    if not reports:
        st.caption("No profitability report found yet. Run `python -m weather_bot.jobs.profitability_report --days-back 30`.")
    else:
        latest = reports[-1]
        st.caption(str(latest))
        st.markdown(latest.read_text())


def tab_status():
    if show_help:
        with st.expander("ℹ️ How to Read This Tab", expanded=False):
            st.markdown(help_text.STATUS_TAB)

    health = queries.latest_health()
    if health.empty:
        st.warning("No health-check rows yet. Run `python -m weather_bot.jobs.health_check` to populate.")
        return

    # Top tile row — system-wide
    cols = st.columns(7)
    trade_stations = set(queries.trade_eligible_stations())
    with cols[0]:
        s = overall_status(health, ["DATA"])
        status_pill("DATA", s,
                    value=f"{(health.component.str.startswith('DATA') & (health.status=='GREEN')).sum()}/4 feeds",
                    tooltip="NBM, HRRR, METAR, Kalshi.")
    with cols[1]:
        s = overall_status(health, ["MODEL"], trade_stations)
        sub_rows = health[health.component == "MODEL"]
        if not sub_rows.empty:
            top = sub_rows.iloc[0]
            d = top["detail"] if isinstance(top["detail"], dict) else json.loads(top["detail"] or "{}")
            sub = f"Brier {d.get('brier_7d', '—')} · Δedge ${d.get('edge_diff_per_fill', '—')}/fill"
        else:
            sub = "no settled fills yet"
        status_pill("MODEL", s, sub=sub,
                    tooltip="Brier score + |model-claimed - realized| edge per settled fill, last 7d.")
    with cols[2]:
        s = overall_status(health, ["MARKETS"], trade_stations)
        n_tot = sum(int(json.loads(r if isinstance(r, str) else json.dumps(r)).get("n_open", 0))
                    for r in health[health.component == "MARKETS"]["detail"].tolist())
        status_pill("MARKETS", s, sub=f"{n_tot} open", tooltip="Open Kalshi markets, today onward.")
    with cols[3]:
        s = overall_status(health, ["RISK"], trade_stations)
        risk_rows = health[health.component == "RISK"]
        notional = sum(json.loads(r if isinstance(r, str) else json.dumps(r)).get("open_notional", 0)
                       for r in risk_rows["detail"].tolist())
        status_pill("RISK", s, value=f"${notional:.0f} open",
                    tooltip="Sum of open paper-fill notional. Capped vs bankroll.")
    with cols[4]:
        pnl_rows = health[health.component == "PNL"]
        s = overall_status(health, ["PNL"], trade_stations)
        net = sum(json.loads(r if isinstance(r, str) else json.dumps(r)).get("net_7d", 0)
                  for r in pnl_rows["detail"].tolist())
        yest = queries.pnl_yesterday()
        if yest["net"] is not None:
            yest_sub = f"yesterday {yest['net']:+,.2f} ({yest['n_wins']}/{yest['n_fills']} wins)"
        else:
            yest_sub = "yesterday —"
        status_pill("P&L 7D", s, value=f"${net:+,.2f}", sub=yest_sub,
                    tooltip="Net P&L on settled fills, last 7 days. Yesterday = prior calendar day settled fills.")
    with cols[5]:
        today = queries.pnl_today()
        t_net = today["net"]
        t_s = "GREEN" if t_net > 0 else ("RED" if t_net < -5 else "GREY")
        status_pill("TODAY", t_s, value=f"${t_net:+,.2f}",
                    sub=f"${today['realized']:+.2f} settled · ${today['unrealized']:+.2f} MtM",
                    tooltip="Settled P&L + mark-to-market on still-open positions for today's valid_date.")
    with cols[6]:
        red_alerts = health[(health.status == "RED") & (health.acknowledged_at.isna())]
        s = "RED" if len(red_alerts) > 0 else "GREEN"
        status_pill("ALERTS", s, value=str(len(red_alerts)),
                    tooltip="Unacknowledged RED alerts blocking trades.")

    st.divider()

    # Detailed health table with ack buttons
    st.subheader("Detail Rows", anchor=False,
                 help="One row per (station, component). Acknowledging clears the trade-loop block.")
    display = health.copy()
    display["status"] = display["status"].map(lambda s: f"{EMOJI.get(s,'')} {s}")
    display["detail"] = display["detail"].apply(
        lambda d: json.dumps(d) if isinstance(d, dict) else d
    )
    display = display[["ts", "station", "component", "status", "metric_value", "detail"]]
    display.columns = ["evaluated_at", "station", "component", "status", "metric", "detail (JSON)"]
    st.dataframe(display, use_container_width=True, hide_index=True)

    # Ack panel
    unacked_red = health[(health.status == "RED") & (health.acknowledged_at.isna())]
    if not unacked_red.empty:
        st.warning(f"{len(unacked_red)} unacknowledged RED alert(s) blocking trades on those stations.")
        for _, row in unacked_red.iterrows():
            cols = st.columns([4, 1])
            with cols[0]:
                st.write(f"🔴 **{row['station']} / {row['component']}** — metric={row['metric_value']}")
            with cols[1]:
                if st.button("Ack", key=f"ack_{row['station']}_{row['component']}",
                             help="Mark as reviewed; trade loop will resume on this station."):
                    _ack_alert(row["station"], row["component"])
                    st.rerun()


def _ack_alert(station: str, component: str):
    sql = """UPDATE health_check
                SET acknowledged_at = now(),
                    acknowledged_by = 'dashboard'
              WHERE station=%s AND component=%s
                AND ts = (SELECT MAX(ts) FROM health_check
                           WHERE station=%s AND component=%s)
                AND acknowledged_at IS NULL"""
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (station, component, station, component))
        conn.commit()
    queries.clear_cache()


def tab_calibration():
    if show_help:
        with st.expander("ℹ️ How to Read This Tab", expanded=False):
            st.markdown(help_text.CALIBRATION_TAB)

    daily = queries.daily_calibration(days_back=14)
    if daily.empty:
        st.info("No settled fills yet in last 14 days.")
        return

    daily["edge_diff"] = daily["realized"] - daily["expected"]
    daily["edge_diff_per_fill"] = daily["edge_diff"] / daily["n"]

    trust_label, trust_detail, trust_color = _model_trust_summary()
    avg_gap = float(daily["edge_diff_per_fill"].mean()) if not daily.empty else 0.0
    if avg_gap < -4:
        gap_label = "Overconfident"
        gap_color = COLOR["RED"]
        gap_sub = "claimed more edge than reality delivered"
    elif avg_gap > 4:
        gap_label = "Underconfident"
        gap_color = COLOR["AMBER"]
        gap_sub = "may be passing on too much edge"
    else:
        gap_label = "Close"
        gap_color = COLOR["GREEN"]
        gap_sub = "claimed edge and actual results are near each other"

    st.subheader("Plain-English Calibration Summary")
    summary_cols = st.columns(3)
    with summary_cols[0]:
        metric_card("Can I trust the odds?", trust_label, trust_detail, trust_color)
    with summary_cols[1]:
        metric_card("Profit claim", gap_label, f"{avg_gap:+.2f} dollars per fill · {gap_sub}", gap_color)
    with summary_cols[2]:
        total_fills = int(daily["n"].sum())
        metric_card("Evidence", f"{total_fills} fills", "last 14 settled days")

    if trust_label == "Low" or gap_label == "Overconfident":
        callout(
            "Bottom line",
            "The bot is still learning. Treat these probabilities as research signals, not live-money odds.",
            COLOR["AMBER"],
        )
    else:
        callout(
            "Bottom line",
            "Calibration is not flashing danger right now, but keep watching the simple summary before trusting size.",
            COLOR["GREEN"],
        )

    st.divider()

    # Edge-gap line chart with threshold band
    st.subheader("Daily Model-Claimed vs Realized Edge",
                 help=help_text.METRIC_TOOLTIPS["edge_gap"])
    fig = go.Figure()
    for station in daily["station"].unique():
        sub = daily[daily["station"] == station]
        fig.add_trace(go.Scatter(x=sub["valid_date"], y=sub["edge_diff_per_fill"],
                                  mode="lines+markers", name=station))
    fig.add_hline(y=4, line_dash="dot", line_color="orange",
                   annotation_text="AMBER ($4/fill)", annotation_position="right")
    fig.add_hline(y=-4, line_dash="dot", line_color="orange")
    fig.add_hline(y=8, line_dash="dash", line_color="red",
                   annotation_text="RED ($8/fill)", annotation_position="right")
    fig.add_hline(y=-8, line_dash="dash", line_color="red")
    fig.add_hline(y=0, line_color="grey", opacity=0.3)
    fig.update_layout(yaxis_title="$/fill (realized - model-claimed)", xaxis_title="valid_date",
                       height=350, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)

    # Reliability diagram
    st.subheader("Fill-Weighted Reliability Diagram (Last 30 Days)",
                 help="Forecast probability deciles vs realized win frequency. "
                      "On a calibrated model, points sit on the diagonal. "
                      "Fill-weighted view can over-count one weather event when "
                      "several fills were opened on the same station/date.")
    rel = queries.reliability_bins(days_back=30)
    if rel.empty:
        st.caption("Not enough data yet.")
    else:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines",
                                   line=dict(color="grey", dash="dot"),
                                   name="perfect", showlegend=False))
        for station in rel["station"].unique():
            sub = rel[rel["station"] == station]
            fig2.add_trace(go.Scatter(x=sub["mean_pred"], y=sub["observed_freq"],
                                       mode="lines+markers",
                                       marker=dict(size=sub["n"]*2 + 4),
                                       name=station, hovertemplate="pred=%{x:.2f}<br>obs=%{y:.2f}<br>n=%{marker.size}"))
        fig2.update_layout(xaxis_title="forecast P", yaxis_title="observed freq",
                            xaxis=dict(range=[0,1]), yaxis=dict(range=[0,1]),
                            height=380, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Event-Weighted Reliability Diagram (Last 30 Days)",
                 help="Same reliability check, but each ticker/side/probability "
                      "bucket contributes one event of weight. This is the more "
                      "honest view when signals or fills are clustered around a "
                      "single station-date.")
    erel = queries.event_reliability_bins(days_back=30)
    if erel.empty:
        st.caption("Not enough settled signal outcomes yet.")
    else:
        fig_event = go.Figure()
        fig_event.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines",
                                       line=dict(color="grey", dash="dot"),
                                       name="perfect", showlegend=False))
        for station in erel["station"].unique():
            sub = erel[erel["station"] == station]
            marker_size = sub["n_events"].astype(float).clip(lower=1) * 3 + 4
            fig_event.add_trace(go.Scatter(
                x=sub["mean_pred"], y=sub["observed_freq"],
                mode="lines+markers",
                marker=dict(size=marker_size),
                name=station,
                customdata=sub[["n_events", "n_signals"]],
                hovertemplate=(
                    "pred=%{x:.2f}<br>obs=%{y:.2f}<br>"
                    "events=%{customdata[0]:.1f}<br>signals=%{customdata[1]}<extra></extra>"
                ),
            ))
        fig_event.update_layout(xaxis_title="forecast P", yaxis_title="observed freq",
                                xaxis=dict(range=[0,1]), yaxis=dict(range=[0,1]),
                                height=380, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig_event, use_container_width=True)

    st.subheader("Signal-Based YES Probability Calibration Map (Last 60 Days)",
                 help="Logged YES bucket probability vs actual bucket outcome, "
                      "event-weighted so repeated scoring of the same market "
                      "does not dominate. This is the evidence table used by "
                      "the live probability calibrator before sizing.")
    ycal = queries.yes_probability_calibration(days_back=60)
    if ycal.empty:
        st.caption("No settled signal outcomes available for YES-probability calibration.")
    else:
        ycal = ycal.copy()
        ycal["bucket"] = ycal["bin"].apply(lambda b: f"{(int(b)-1)*10:>2d}-{int(b)*10:>3d}%")
        ycal["gap"] = ycal["mean_pred"] - ycal["observed_freq"]
        st.dataframe(
            ycal[["station", "bucket", "n", "mean_pred", "observed_freq", "gap", "n_yes"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "n": st.column_config.NumberColumn("effective events", format="%.1f"),
                "mean_pred": st.column_config.NumberColumn("predicted", format="%.3f"),
                "observed_freq": st.column_config.NumberColumn("observed", format="%.3f"),
                "gap": st.column_config.NumberColumn("pred-observed", format="%+.3f"),
                "n_yes": st.column_config.NumberColumn("YES events", format="%.1f"),
            },
        )

    # Per-bucket calibration table — diagnose WHICH probability ranges are off
    st.subheader("Per-Bucket Calibration (Last 30 Days)",
                 help="Each row = one 10% probability bucket. ⚠ marks rows where "
                      "predicted probability lands outside the 95% CI of observed wins — "
                      "the model is systematically miscalibrated at that range.")
    bcal = queries.bucket_calibration(days_back=30, n_bins=10)
    if bcal.empty:
        st.caption("Not enough settled fills yet.")
    else:
        # Wilson 95% CI on observed_freq for each bin.
        import math
        z = 1.96
        def wilson(k, n):
            if n == 0: return None, None
            phat = k / n
            denom = 1 + z*z/n
            center = (phat + z*z/(2*n)) / denom
            half = z * math.sqrt(phat*(1-phat)/n + z*z/(4*n*n)) / denom
            return max(0.0, center - half), min(1.0, center + half)

        bcal = bcal.copy()
        bcal["bin_range"] = bcal["bin"].apply(lambda b: f"{(int(b)-1)*10:>2d}–{int(b)*10:>3d}%")
        ci = bcal.apply(lambda r: wilson(int(r["n_won"] or 0), int(r["n"] or 0)), axis=1)
        bcal["ci_lo"] = [c[0] for c in ci]
        bcal["ci_hi"] = [c[1] for c in ci]
        bcal["gap"] = bcal["mean_pred"] - bcal["observed_freq"]
        bcal["miscalibrated"] = bcal.apply(
            lambda r: "⚠" if (r["ci_lo"] is not None and
                                (r["mean_pred"] < r["ci_lo"] or r["mean_pred"] > r["ci_hi"]))
                       else "", axis=1
        )
        display = bcal[["bin_range", "n", "mean_pred", "observed_freq",
                          "ci_lo", "ci_hi", "gap", "brier_bin", "miscalibrated"]]
        display.columns = ["bucket", "n", "predicted", "observed", "obs_CI_lo",
                            "obs_CI_hi", "gap (pred−obs)", "brier", ""]
        st.dataframe(display, use_container_width=True, hide_index=True,
                      column_config={
                          "predicted": st.column_config.NumberColumn(format="%.3f"),
                          "observed":  st.column_config.NumberColumn(format="%.3f"),
                          "obs_CI_lo": st.column_config.NumberColumn(format="%.3f"),
                          "obs_CI_hi": st.column_config.NumberColumn(format="%.3f"),
                          "gap (pred−obs)": st.column_config.NumberColumn(format="%+.3f"),
                          "brier":     st.column_config.NumberColumn(format="%.4f"),
                      })

    # ---- New-model performance ---------------------------------------------
    # Per-model forecast accuracy vs CLI ground truth. Surfaces the GFS-vs-NBM
    # gap that the 30-day research comparison measured. GFS rows will be sparse
    # until we have a week+ of pull_gfs data.
    st.subheader("Per-Model Forecast Accuracy (vs CLI Truth)",
                 help="Daily |predicted_TMAX − CLI_TMAX| per model. NBM uses p50 of the "
                      "latest run for each valid_date. HRRR/GFS/ECMWF use max(hourly TMP_2M) "
                      "from the latest run. CLI is the Kalshi NHIGH settlement source.")
    macc = queries.model_accuracy(days_back=14)
    if macc.empty:
        st.caption("No CLI truth + forecast overlap yet. (CLI capture started 2026-05-01.)")
    else:
        # Aggregate bar chart: mean MAE per (station, model)
        agg = macc.groupby(["station", "model"]).agg(
            n=("abs_err", "size"), mae=("abs_err", "mean")
        ).reset_index()
        fig = px.bar(agg, x="station", y="mae", color="model", barmode="group",
                      text=agg["mae"].round(2),
                      labels={"mae": "MAE (°F)", "station": "Station", "model": "Model"},
                      color_discrete_map={"NBM": "#2563eb", "HRRR": "#f59e0b",
                                          "GFS": "#16a34a", "ECMWF": "#9333ea"})
        fig.update_traces(textposition="outside")
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                           legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Sample sizes (days of overlap): " +
                    " · ".join(f"{r['station']}/{r['model']}={int(r['n'])}" for _, r in agg.iterrows()))

        # Per-day line chart so trends + outliers are visible
        if len(macc["valid_date"].unique()) >= 3:
            macc["station_model"] = macc["station"] + " " + macc["model"]
            fig2 = px.line(macc, x="valid_date", y="abs_err", color="station_model",
                            markers=True,
                            labels={"abs_err": "|err| (°F)", "valid_date": "Date",
                                    "station_model": "Station / Model"})
            fig2.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                                legend=dict(orientation="h", y=1.15))
            st.plotly_chart(fig2, use_container_width=True)

    # Today's model-agreement distribution — what the agreement gate would see.
    st.subheader("Today's Model Agreement",
                 help="Each signal carries a directional vote per model (NBM, HRRR, GFS) "
                      "on the bucket. This shows today's signals grouped by agreement "
                      "tally and bot's chosen side. With REQUIRE_AGREEMENT_N=2, signals "
                      "where fewer than 2 models agree with the bot's side would be "
                      "blocked with skip_reason='AGREEMENT'.")
    vd = queries.vote_distribution_today()
    if vd.empty:
        st.caption("No signals with model votes yet today.")
    else:
        # Build a label like "0Y/3N" and a "with us / against us" tag.
        vd = vd.copy()
        vd["votes"] = vd["n_yes"].astype(str) + "Y/" + vd["n_no"].astype(str) + "N"
        def _agreement_tag(r):
            same = r["n_yes"] if r["side"] == "YES" else r["n_no"]
            if same >= 2: return "models agree (≥2 with bot)"
            if (3 - same) >= 2: return "models against (≥2 vs bot)"
            return "split"
        vd["agreement"] = vd.apply(_agreement_tag, axis=1)
        # Stacked bar: x = OPEN/SKIP, color = agreement, height = signals
        agg = vd.groupby(["action", "agreement"])["n"].sum().reset_index()
        fig3 = px.bar(agg, x="action", y="n", color="agreement", barmode="stack",
                       text="n",
                       color_discrete_map={
                           "models agree (≥2 with bot)": "#16a34a",
                           "split": "#737373",
                           "models against (≥2 vs bot)": "#dc2626",
                       },
                       labels={"n": "# signals", "action": "Action"})
        fig3.update_traces(textposition="inside")
        fig3.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                            legend=dict(orientation="h", y=1.15))
        st.plotly_chart(fig3, use_container_width=True)

        # Quick summary numbers above the chart
        opens = vd[vd["action"] == "OPEN"]
        n_open = int(opens["n"].sum())
        n_against = int(opens[opens["agreement"] == "models against (≥2 vs bot)"]["n"].sum())
        n_with = int(opens[opens["agreement"] == "models agree (≥2 with bot)"]["n"].sum())
        if n_open > 0:
            pct_against = 100 * n_against / n_open
            st.caption(
                f"**Today's OPENs**: {n_open} total · "
                f"**{n_with} with ≥2 models agreeing** · "
                f"**{n_against} against ≥2 models** "
                f"({pct_against:.0f}% of OPENs would be blocked if `REQUIRE_AGREEMENT_N=2`)"
            )
            st.info(
                "📊 **Backtest finding (2026-05-02)**: Enabling the agreement gate would have "
                "**cost ~$240 over the past 30 days**. The bot's against-consensus trades are "
                "concentrated in long-shot prices where the model votes don't capture the "
                "asymmetric payoff. Win rate ≠ profit. See "
                "`research/reports/backtest_agreement_gate_*.md`. Gate stays **off**."
            )

    # Bias drift events
    st.subheader("Bias Drift Events (Last 7 Days)",
                 help=help_text.METRIC_TOOLTIPS["delta_sigma"])
    drift = queries.bias_drift_recent(hours=24*7)
    if drift.empty:
        st.success("✅ No drift events. Bias table is stable.")
    else:
        st.dataframe(drift, use_container_width=True, hide_index=True)


def _leaning(row) -> tuple[str, str]:
    """(label, hex-color) for an open position — kept as compatibility shim
    for any callers expecting the old leaning string. Prefer _settlement_state()."""
    state, color, _ = _settlement_state(row)
    return state, color


def _settlement_state(row) -> tuple[str, str, str]:
    """Five-state settlement-confidence classifier for a single position row.

    Returns (badge_label, hex_color, explanation). States ordered by certainty:

      LOCKED ✓ / LOCKED ✗ — outcome already determined by observed TMAX/TMIN
                            crossing the bucket boundary in a one-way direction.
                            Outcome cannot reverse before settlement.
      LEANING WIN / LOSS  — p50 forecast points to a side and isn't on the
                            bucket boundary (within ±1.5°F). Likely but not
                            locked.
      COIN-FLIP           — p50 within ±1.5°F of a bucket edge — outcome
                            sensitive to small forecast revisions.
      WAITING             — past valid_date but no obs/CLI captured yet (rare;
                            data pipeline issue).
      —                   — no obs and no p50 yet (future date, early ingest).
    """
    var = row.get("var", "")
    obs_val = row.get("obs_tmax") if "TMAX" in var else row.get("obs_tmin")
    p50 = row.get("p50")
    cli_tmax = row.get("cli_tmax") if "TMAX" in var else row.get("cli_tmin")
    lower_f = row.get("lower_f")
    upper_f = row.get("upper_f")
    side = row.get("side", "YES")
    days_to_settle = row.get("days_to_settle", 0)

    lo = lower_f if lower_f is not None else float("-inf")
    hi = upper_f if upper_f is not None else float("inf")

    def _outcome_label(yes_wins: bool, prefix: str, color: str) -> tuple[str, str]:
        outcome_win = yes_wins if side == "YES" else not yes_wins
        return f"{prefix} {'✓' if outcome_win else '✗'}", color

    # 1. LOCKED — observed extreme already determines outcome
    if obs_val is not None:
        if "TMAX" in var and obs_val >= hi:
            badge, color = _outcome_label(False, "LOCKED", "#16a34a" if side == "NO" else "#dc2626")
            return badge, color, f"obs TMAX {obs_val:.1f}°F already exceeded bucket ceiling {hi:.1f}°F"
        if "TMIN" in var and obs_val < lo:
            badge, color = _outcome_label(False, "LOCKED", "#16a34a" if side == "NO" else "#dc2626")
            return badge, color, f"obs TMIN {obs_val:.1f}°F already dropped below bucket floor {lo:.1f}°F"

    # 2. CLI captured but bot is still showing as open — settlement job hasn't
    #    swept yet. Display the CLI-implied outcome.
    if cli_tmax is not None:
        cli_in = lo <= cli_tmax < hi
        badge, color = _outcome_label(cli_in, "CLI", "#16a34a" if (cli_in == (side == "YES")) else "#dc2626")
        return badge, color, f"CLI TMAX {cli_tmax:.1f}°F (settlement job pending)"

    # 3. Past valid_date but no CLI yet — waiting on settlement data
    if days_to_settle < 0:
        return "WAITING", "#737373", "valid_date passed; awaiting CLI / METAR"

    # 4. p50 LEANING / COIN-FLIP
    if p50 is not None:
        on_boundary = ((lower_f is not None and abs(p50 - lo) < 1.5)
                        or (upper_f is not None and abs(p50 - hi) < 1.5))
        if on_boundary:
            return "COIN-FLIP", "#f59e0b", f"p50 {p50:.1f}°F within 1.5°F of bucket edge"
        yes_wins = lo <= p50 < hi
        outcome_win = yes_wins if side == "YES" else not yes_wins
        color = "#16a34a" if outcome_win else "#dc2626"
        return ("LEANING WIN" if outcome_win else "LEANING LOSS"), color, f"p50 {p50:.1f}°F"

    # 5. No information yet
    return "—", "#737373", "no obs or forecast yet"


def tab_trading():
    if show_help:
        with st.expander("ℹ️ How to Read This Tab", expanded=False):
            st.markdown(help_text.TRADING_TAB)

    # ---- Regional Snapshot (Sprint 1: triangulation + atmos + rate-of-change) -
    # For each trade-eligible station, show:
    #   - current temp + rate-of-change (warming/cooling now)
    #   - regional spread + primary-vs-mean (is primary running warm/cool vs
    #     surrounding airports? sometimes signals incoming temperature shift)
    #   - atmospheric features (BL height, cloud, 925mb, peak solar) — the
    #     signals that drive whether TMAX overshoots or undershoots forecast
    st.subheader("Regional Snapshot",
                 help="Per trade-station live read: current temp + rate-of-change, "
                      "regional spread vs neighbor airports, plus atmospheric "
                      "drivers (boundary layer height, cloud cover, 925mb temp, "
                      "peak solar). Inspired by dailydewpoint.com's NYC observation "
                      "panel — surfaces 'is the temperature field moving' info that "
                      "single-station METAR misses.")
    today = date.today()
    snapshot_cols = st.columns(len(queries.trade_eligible_stations()))
    for col, st_code in zip(snapshot_cols, queries.trade_eligible_stations()):
        with col:
            rate = queries.temp_rate_of_change(st_code)
            field = queries.regional_temp_field(st_code)
            atmos = queries.atmos_daily_features(st_code, today)
            overnight = queries.nws_overnight_jump(st_code, today)

            primary_temp = field["primary_temp"] if field else (rate["last_temp_f"] if rate else None)
            rate_str = ""
            if rate is not None:
                if rate.get("rate_f_per_hr") is None:
                    rate_str = " rate suppressed"
                else:
                    arrow = "↑" if rate["rate_f_per_hr"] > 0.1 else ("↓" if rate["rate_f_per_hr"] < -0.1 else "→")
                    rate_str = f" {arrow}{abs(rate['rate_f_per_hr']):.1f}°/hr"

            header = f"### {st_code}"
            if primary_temp is not None:
                header += f" — {primary_temp:.1f}°F{rate_str}"
            st.markdown(header)

            if field is not None and field["n_stations"] > 1:
                vs_mean_arrow = "warmer" if field["vs_mean"] > 0 else "cooler"
                st.markdown(
                    f"**Regional** ({field['n_stations']} stns): "
                    f"mean {field['mean']:.1f}°F · spread {field['spread']:.1f}°F · "
                    f"primary **{abs(field['vs_mean']):.1f}°F {vs_mean_arrow}** vs mean"
                )
                # Per-neighbor offsets (compact)
                if field["neighbors"]:
                    parts = [f"{n['code'][1:]}:{n['vs_primary']:+.1f}" for n in field["neighbors"][:6]]
                    st.caption("vs primary: " + " · ".join(parts))
            else:
                st.caption("No regional neighbors with recent data.")

            if atmos is not None:
                bl = atmos["bl_peak_m"]
                bl_label = "deep" if bl and bl > 2000 else ("shallow" if bl and bl < 800 else "moderate")
                cloud = atmos["cloud_mean_pct"]
                cloud_label = "clear" if cloud and cloud < 30 else ("mostly cloudy" if cloud and cloud > 70 else "mixed")
                st.markdown(
                    f"**Atmos**: BL **{bl:.0f}m** ({bl_label}) · "
                    f"cloud **{cloud:.0f}%** ({cloud_label}) · "
                    f"925mb **{atmos['tmp_925_mean_f']:.1f}°F** · "
                    f"solar peak **{atmos['solar_peak_w_m2']:.0f} W/m²**"
                )
            else:
                st.caption("No atmos data yet (pull_atmos hasn't run).")

            # NWS overnight jump — yesterday's last vs today's first NBM forecast
            if overnight is not None:
                jump = overnight["jump_f"]
                if abs(jump) >= 1.0:
                    arrow = "🔺" if jump > 0 else "🔻"
                    st.markdown(
                        f"**NWS overnight jump**: {arrow} **{jump:+.1f}°F** "
                        f"({overnight['yesterday_last_f']:.1f} → {overnight['today_first_f']:.1f})"
                    )
                else:
                    st.caption(
                        f"NWS overnight jump: {jump:+.1f}°F "
                        f"({overnight['yesterday_last_f']:.1f} → {overnight['today_first_f']:.1f}, stable)"
                    )

            # Forecast audit trail — drill down to see how predictions evolved
            with st.expander(f"Forecast audit log ({st_code} today)", expanded=False):
                audit = queries.forecast_audit_log(st_code, today)
                if audit.empty:
                    st.caption("No forecasts logged for today yet.")
                else:
                    st.caption(f"{len(audit)} forecast issuances chronologically (NBM p50, HRRR/GFS/ECMWF daily-MAX)")
                    audit["forecast_f"] = audit["forecast_f"].round(1)
                    st.dataframe(audit, use_container_width=True, hide_index=True, height=240)
    st.divider()

    # Open positions
    st.subheader("Open Positions", help=help_text.METRIC_TOOLTIPS["open_n"])
    pos = queries.open_positions_with_obs()
    if pos.empty:
        st.info("No open positions.")
    else:
        def _mtm(r):
            if pd.isna(r.yes_ask) or pd.isna(r.yes_bid):
                return None
            cur_price = float(r.yes_ask) if r.side == "YES" else (1.0 - float(r.yes_bid))
            return float((cur_price - float(r.price)) * r.contracts)

        pos["mtm"] = pos.apply(_mtm, axis=1)
        # Five-state settlement-confidence classification per position.
        states = pos.apply(lambda r: pd.Series(_settlement_state(r),
                                                  index=["state", "_state_color", "_state_explain"]),
                           axis=1)
        pos = pd.concat([pos, states], axis=1)

        # Render as HTML table so we can colorize the state column.
        rows_html = []
        for _, r in pos.iterrows():
            obs_val = r.get("obs_tmax") if "TMAX" in str(r.get("var", "")) else r.get("obs_tmin")
            obs_str = f"{obs_val:.1f}°F" if pd.notna(obs_val) else "—"
            p50_str = f"{r['p50']:.1f}°F" if pd.notna(r.get("p50")) else "—"
            mtm_str = f"${r['mtm']:+.2f}" if r["mtm"] is not None else "—"
            state_label, state_color, state_explain = r["state"], r["_state_color"], r["_state_explain"]
            bucket = _bucket_label(r.get("lower_f"), r.get("upper_f"))
            rows_html.append(
                f"<tr>"
                f"<td><strong>{bucket}</strong></td><td>{r['side']}</td>"
                f"<td>{r['price']:.2f}</td><td>{r['contracts']}</td>"
                f"<td>{r['valid_date']}</td><td>{r['days_to_settle']}d</td>"
                f"<td>{obs_str}</td><td>{p50_str}</td><td>{mtm_str}</td>"
                f"<td title='{state_explain}' style='color:{state_color};font-weight:600'>{state_label}</td>"
                f"<td style='opacity:0.5;font-size:0.8em'>{r['ticker']}</td>"
                f"</tr>"
            )
        header = ("<table style='width:100%;font-size:0.85em;border-collapse:collapse'>"
                  "<thead><tr style='border-bottom:1px solid #444'>"
                  "<th>bucket</th><th>side</th><th>price</th><th>contracts</th>"
                  "<th>valid_date</th><th>days</th>"
                  "<th>obs so far</th><th>p50</th><th>MtM</th><th>settlement</th><th>ticker</th>"
                  "</tr></thead><tbody>")
        st.markdown(header + "".join(rows_html) + "</tbody></table>", unsafe_allow_html=True)
        st.caption("settlement state: **LOCKED** (obs already decides outcome) · "
                   "**CLI** (settlement source captured, settle job pending) · "
                   "**LEANING** (p50 indicates direction) · "
                   "**COIN-FLIP** (p50 within 1.5°F of bucket edge) · "
                   "**WAITING** (past valid_date, no obs/CLI yet) · "
                   "hover for explanation.")

    # 7-day SKIP breakdown — surfaces why most signals don't become trades
    st.subheader("SKIP Breakdown (Last 7 Days)",
                 help="Why most signals don't become trades. FEE_LOAD = fee/price > 20%; "
                      "NO_EDGE = edge below threshold; DIVERGENCE = |fair − market| > 0.50; "
                      "BIAS_GATE = bias table missing/thin/stale; TRIPWIRE_RED = health-check block; "
                      "PROFIT_GATE = blocked by profitability controls.")
    breakdown = queries.skip_breakdown(days_back=7)
    if breakdown.empty:
        st.caption("No SKIP signals in the last 7 days.")
    else:
        bd_cols = st.columns(len(breakdown))
        for col, (_, row) in zip(bd_cols, breakdown.iterrows()):
            col.metric(row["skip_reason"], int(row["n"]),
                        help=f"{int(row['n_tickers'])} distinct tickers")

    # Today's signals
    st.subheader("Signals Today (Every Tick)",
                 help="Every market scored by the trade loop today. "
                      "Filter by action and skip_reason to see what was opened vs skipped.")
    sigs = queries.signals_today()
    if sigs.empty:
        st.info("No signals scored yet today.")
    else:
        sf_cols = st.columns(2)
        with sf_cols[0]:
            action_filter = st.multiselect("Action", options=sorted(sigs["action"].unique()),
                                            default=sorted(sigs["action"].unique()),
                                            help="OPEN = paper-filled. SKIP = refused.")
        with sf_cols[1]:
            skip_options = sorted([str(r) for r in sigs["skip_reason"].dropna().unique()])
            skip_filter = st.multiselect("Skip reason", options=skip_options,
                                          default=skip_options,
                                          help="Only applies when 'SKIP' is in Action filter.")
        sigs_f = sigs[sigs["action"].isin(action_filter)]
        if "SKIP" in action_filter and skip_options:
            sigs_f = sigs_f[(sigs_f["action"] != "SKIP") | (sigs_f["skip_reason"].isin(skip_filter))]
        sigs_f["divergence"] = (sigs_f["fair_prob"] -
                                 (sigs_f["market_ask"].fillna(0) + sigs_f["market_bid"].fillna(0))/2).abs()

        def _fmt_votes(v):
            if not v or not isinstance(v, dict):
                return ""
            short = {"YES": "Y", "NO": "N", "NA": "—"}
            parts = [f"{m}:{short.get(v.get(m), '?')}" for m in ("NBM", "HRRR", "GFS") if m in v]
            tail = f" ({v.get('n_yes',0)}Y/{v.get('n_no',0)}N)"
            return " ".join(parts) + tail
        sigs_f["votes"] = sigs_f["model_votes"].apply(_fmt_votes)

        def _fmt_risk(rr):
            if not rr or not isinstance(rr, dict):
                return ""
            label = rr.get("label", "?")
            score = rr.get("score", 0)
            emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(label, "")
            return f"{emoji} {label} ({score:.2f})"
        sigs_f["risk"] = sigs_f["reversal_risk"].apply(_fmt_risk)
        sigs_f["bucket"] = sigs_f.apply(lambda r: _bucket_label(r.get("lower_f"), r.get("upper_f")), axis=1)

        st.dataframe(sigs_f[["ts", "bucket", "station", "var", "side", "fair_prob",
                              "market_ask", "market_bid", "divergence", "edge", "size_usd",
                              "action", "skip_reason", "votes", "risk", "notes", "ticker"]],
                      use_container_width=True, hide_index=True)

    # Distribution preview
    st.subheader("Distribution Preview (Today)",
                 help="Current NBM CDF after bias correction + HRRR blend, "
                      "with Kalshi market buckets shaded. Eyeball check: are we agreeing with the market?")
    today = date.today()
    for station in queries.trade_eligible_stations():
        with st.expander(f"{station} — TMAX_DAILY for {today}"):
            inputs = queries.latest_distribution_inputs(station, today, "TMAX_DAILY")
            buckets = queries.kalshi_buckets_today(station, today, "TMAX_DAILY")
            _render_distribution(station, today, "TMAX_DAILY", inputs, buckets)


def _render_distribution(station, target_date, var, inputs, buckets):
    nbm = inputs.get("nbm")
    if nbm is None or nbm.empty:
        st.caption("No NBM data for today yet.")
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=nbm["value"], y=nbm["percentile"]/100.0,
                              mode="lines+markers", name="NBM CDF",
                              line=dict(color="steelblue")))
    hrrr = inputs.get("hrrr")
    if hrrr is not None and not hrrr.empty and hrrr["tmax"].iloc[0] is not None:
        fig.add_vline(x=float(hrrr["tmax"].iloc[0]), line_color="orange", line_dash="dash",
                       annotation_text=f"HRRR {hrrr['tmax'].iloc[0]:.1f}°F")
    if buckets is not None and not buckets.empty:
        for _, b in buckets.iterrows():
            lo = b["lower_f"] if b["lower_f"] is not None else nbm["value"].min() - 5
            hi = b["upper_f"] if b["upper_f"] is not None else nbm["value"].max() + 5
            fig.add_vrect(x0=lo, x1=hi, fillcolor="lightgreen", opacity=0.08,
                           line_width=0)
    fig.update_layout(xaxis_title="°F", yaxis_title="P(TMAX ≤ x)",
                       height=300, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)


def tab_deep_dive():
    if show_help:
        with st.expander("ℹ️ How to Read This Tab", expanded=False):
            st.markdown(help_text.DEEP_DIVE_TAB)

    st.subheader("Counterfactual Replay",
                 help="Re-score historical settled fills under hypothetical model parameters.")

    cols = st.columns(4)
    with cols[0]:
        days_back = st.number_input("Days back", min_value=3, max_value=60, value=14)
    with cols[1]:
        max_widen = st.slider("Max widen factor", 1.0, 2.0, 1.10, 0.05,
                               help=help_text.METRIC_TOOLTIPS.get("brier", ""))
    with cols[2]:
        hrrr_cap = st.slider("HRRR weight cap", 0.0, 0.95, 0.95, 0.05)
    with cols[3]:
        div_max = st.slider("Divergence max", 0.20, 0.80, 0.50, 0.05)
    apply_hrrr_bias = st.checkbox("Apply HRRR bias correction", value=False,
                                    help="Production: off (HRRR bias still small-sample noisy).")
    if st.button("▶ Run replay", type="primary"):
        params = replay_engine.ReplayParams(
            max_widen=max_widen, hrrr_weight_cap=hrrr_cap,
            divergence_max=div_max, apply_hrrr_bias=apply_hrrr_bias,
        )
        end = date.today()
        start = end - timedelta(days=days_back)
        with st.spinner(f"Replaying {start} → {end} …"):
            df = replay_engine.replay(start, end, params)
        if df.empty:
            st.warning("No settled fills in window.")
        else:
            st.success(f"Replayed {len(df)} settled fills.")
            cols = st.columns(4)
            cols[0].metric("Σ recorded P&L", f"${df['rec_pnl'].sum():+,.2f}")
            cols[1].metric("Σ replay P&L", f"${df['new_pnl'].sum():+,.2f}",
                            delta=f"{df['new_pnl'].sum() - df['rec_pnl'].sum():+,.2f}")
            cols[2].metric("Brier (recorded)", f"{df['brier_recorded'].mean():.4f}",
                            help=help_text.METRIC_TOOLTIPS["brier"])
            cols[3].metric("Brier (replay)", f"{df['brier_replay'].mean():.4f}",
                            delta=f"{df['brier_replay'].mean() - df['brier_recorded'].mean():+.4f}",
                            delta_color="inverse")
            st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    # NBM cycle inspector
    st.subheader("NBM Cycle Inspector",
                 help="Adjacent cycles disagreeing by >3°F is the visible signature of a data ingestion bug.")
    cols = st.columns(3)
    with cols[0]:
        insp_station = st.selectbox("Station", queries.fetch_stations())
    with cols[1]:
        insp_date = st.date_input("Valid date", value=date.today())
    with cols[2]:
        insp_var = st.selectbox("Var", ["TMAX_DAILY", "TMIN_DAILY"])
    cycles = queries.nbm_cycles_for(insp_station, insp_date, insp_var)
    if cycles.empty:
        st.caption("No cycles for that selection.")
    else:
        fig = go.Figure()
        for run, sub in cycles.groupby("run_time"):
            sub = sub.sort_values("percentile")
            fig.add_trace(go.Scatter(x=sub["value"], y=sub["percentile"]/100.0,
                                      mode="lines+markers", name=str(run)))
        fig.update_layout(xaxis_title="°F", yaxis_title="P(X ≤ x)",
                           height=400, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
        # Cycle-to-cycle median divergence (the canary)
        medians = cycles[cycles.percentile == 50].set_index("run_time")["value"].sort_index()
        if len(medians) > 1:
            spread = float(medians.max() - medians.min())
            color = "red" if spread > 5 else "orange" if spread > 3 else "green"
            st.markdown(f"**P50 spread across cycles:** :{color}[{spread:.2f}°F]"
                         + " — historical 04-30 corruption was 18°F.")

    st.divider()
    # Per-fill ledger
    st.subheader("Per-Fill Ledger",
                 help="Every settled fill with: divergence at fill time, fair vs market, won/lost, net P&L. "
                      "Sortable. Surfaced from per_fill_ledger() query.")
    ledger = queries.per_fill_ledger(days_back=14)
    if not ledger.empty:
        st.dataframe(ledger, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
st.title("weather_bot Command Center")
if show_help:
    with st.expander("ℹ️ How to Use This Dashboard", expanded=False):
        st.markdown(help_text.OVERVIEW)

# Single-tab render based on sidebar radio selection (no st.tabs eager eval).
TAB_DISPATCH = {
    "Simple":        tab_simple,
    "Home":          tab_home,
    "Trading":       tab_trading,
    "Profitability": tab_profitability,
    "Status":        tab_status,
    "Calibration":   tab_calibration,
    "Deep Dive":     tab_deep_dive,
}
TAB_DISPATCH[selected_tab]()
