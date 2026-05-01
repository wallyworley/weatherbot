"""Streamlit command center for weather_bot.

Run:
    streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501

Auto-refreshes every 15 seconds. Reads directly from Postgres — no service
layer between the DB and the UI by design.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from weather_bot.dashboard import help_text, queries, replay as replay_engine
from weather_bot.data import persistence

st.set_page_config(page_title="weather_bot · command center", layout="wide", page_icon="🌡️")

# Auto-refresh every 15s, but allow user to pause from sidebar.
with st.sidebar:
    st.title("⚙️ controls")
    auto_refresh = st.toggle("Auto-refresh (15s)", value=True,
                              help="Disable when investigating to keep your scroll position.")
    if auto_refresh:
        st_autorefresh(interval=15_000, key="auto_refresh")
    st.caption("Last loaded: " + datetime.now().strftime("%H:%M:%S"))
    st.divider()
    show_help = st.toggle("Show help panels", value=True,
                          help="Expand legend / explanation text on each tab.")
    st.divider()
    st.caption("**Trading:** " + ", ".join(queries.trade_eligible_stations()))
    st.caption("**Gathering data only:** " + ", ".join(s for s in queries.fetch_stations()
                                              if s not in queries.trade_eligible_stations()))
    st.divider()
    with st.expander("📖 Glossary — what these terms mean"):
        st.markdown(help_text.GLOSSARY)


# ---------------------------------------------------------------------------
# Visual helpers
# ---------------------------------------------------------------------------
COLOR = {"GREEN": "#16a34a", "AMBER": "#f59e0b", "RED": "#dc2626", "GREY": "#737373"}
EMOJI = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴"}


def status_pill(label: str, status: str, value: str | None = None, sub: str | None = None,
                tooltip: str | None = None):
    color = COLOR.get(status, COLOR["GREY"])
    emoji = EMOJI.get(status, "⚪")
    sub_html = f"<div style='font-size:0.85em;opacity:0.85'>{sub}</div>" if sub else ""
    val_html = f"<div style='font-size:1.4em;font-weight:600;margin-top:4px'>{value}</div>" if value else ""
    block = f"""
        <div style="background:{color};color:white;padding:14px 18px;border-radius:10px;margin-bottom:8px;">
          <div style='font-size:0.78em;letter-spacing:0.05em;text-transform:uppercase'>{label} {emoji}</div>
          {val_html}
          {sub_html}
        </div>
        """
    st.markdown(block, unsafe_allow_html=True)
    if tooltip:
        st.caption(tooltip)


def overall_status(rows: pd.DataFrame, components: list[str], stations_filter: set[str] | None = None) -> str:
    if rows.empty: return "GREY"
    sub = rows[rows["component"].str.startswith(tuple(components))]
    if stations_filter is not None:
        sub = sub[sub["station"].isin(stations_filter | {"GLOBAL"})]
    if sub.empty: return "GREY"
    if (sub["status"] == "RED").any(): return "RED"
    if (sub["status"] == "AMBER").any(): return "AMBER"
    return "GREEN"


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
def tab_status():
    if show_help:
        with st.expander("ℹ️ How to read this tab", expanded=False):
            st.markdown(help_text.STATUS_TAB)

    health = queries.latest_health()
    if health.empty:
        st.warning("No health-check rows yet. Run `python -m weather_bot.jobs.health_check` to populate.")
        return

    # Top tile row — system-wide
    cols = st.columns(6)
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
                    tooltip="Brier score + |expected − realized| edge per settled fill, last 7d.")
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
        status_pill("P&L 7D", s, value=f"${net:+,.2f}",
                    tooltip="Net P&L on settled fills, last 7 days.")
    with cols[5]:
        red_alerts = health[(health.status == "RED") & (health.acknowledged_at.isna())]
        s = "RED" if len(red_alerts) > 0 else "GREEN"
        status_pill("ALERTS", s, value=str(len(red_alerts)),
                    tooltip="Unacknowledged RED alerts blocking trades.")

    st.divider()

    # Detailed health table with ack buttons
    st.subheader("Detail rows", anchor=False,
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


def tab_calibration():
    if show_help:
        with st.expander("ℹ️ How to read this tab", expanded=False):
            st.markdown(help_text.CALIBRATION_TAB)

    daily = queries.daily_calibration(days_back=14)
    if daily.empty:
        st.info("No settled fills yet in last 14 days.")
        return

    daily["edge_diff"] = daily["realized"] - daily["expected"]
    daily["edge_diff_per_fill"] = daily["edge_diff"] / daily["n"]

    # Edge-gap line chart with threshold band
    st.subheader("Daily expected vs realized edge",
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
    fig.update_layout(yaxis_title="$/fill (realized − expected)", xaxis_title="valid_date",
                       height=350, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)

    # Reliability diagram
    st.subheader("Reliability diagram (last 30 days)",
                 help="Forecast probability deciles vs realized win frequency. "
                      "On a calibrated model, points sit on the diagonal.")
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

    # Bias drift events
    st.subheader("Bias drift events (last 7 days)",
                 help=help_text.METRIC_TOOLTIPS["delta_sigma"])
    drift = queries.bias_drift_recent(hours=24*7)
    if drift.empty:
        st.success("✅ No drift events. Bias table is stable.")
    else:
        st.dataframe(drift, use_container_width=True, hide_index=True)


def tab_trading():
    if show_help:
        with st.expander("ℹ️ How to read this tab", expanded=False):
            st.markdown(help_text.TRADING_TAB)

    # Open positions
    st.subheader("Open positions", help=help_text.METRIC_TOOLTIPS["open_n"])
    pos = queries.open_positions()
    if pos.empty:
        st.info("No open positions.")
    else:
        # Compute mark-to-market: payout-if-correct = 1, current value = market price
        def _mtm(r):
            if pd.isna(r.yes_ask) or pd.isna(r.yes_bid):
                return None
            entry = float(r.price)
            cur_price = float(r.yes_ask) if r.side == "YES" else (1.0 - float(r.yes_bid))
            return float((cur_price - entry) * r.contracts)
        pos["mtm"] = pos.apply(_mtm, axis=1)
        st.dataframe(pos[["ticker", "side", "price", "contracts", "valid_date",
                           "days_to_settle", "yes_ask", "yes_bid", "mtm"]],
                      use_container_width=True, hide_index=True)

    # Today's signals
    st.subheader("Signals today (every tick)",
                 help="Every market scored by the trade loop today. "
                      "Filter by action to see what was opened vs skipped.")
    sigs = queries.signals_today()
    if sigs.empty:
        st.info("No signals scored yet today.")
    else:
        action_filter = st.multiselect("Action", options=sorted(sigs["action"].unique()),
                                        default=sorted(sigs["action"].unique()),
                                        help="OPEN = paper-filled. SKIP/* = various refusal reasons.")
        sigs_f = sigs[sigs["action"].isin(action_filter)]
        sigs_f["divergence"] = (sigs_f["fair_prob"] -
                                 (sigs_f["market_ask"].fillna(0) + sigs_f["market_bid"].fillna(0))/2).abs()
        st.dataframe(sigs_f[["ts", "ticker", "station", "var", "side", "fair_prob",
                              "market_ask", "market_bid", "divergence", "edge", "size_usd",
                              "action", "notes"]],
                      use_container_width=True, hide_index=True)

    # Distribution preview
    st.subheader("Distribution preview (today)",
                 help="Current NBM CDF after bias correction + HRRR blend, "
                      "with Kalshi market buckets shaded. Eyeball check: are we agreeing with the market?")
    today = date.today()
    for station in queries.trade_eligible_stations():
        with st.expander(f"📍 {station} — TMAX_DAILY for {today}"):
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
        with st.expander("ℹ️ How to read this tab", expanded=False):
            st.markdown(help_text.DEEP_DIVE_TAB)

    st.subheader("Counterfactual replay",
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
    st.subheader("NBM cycle inspector",
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
    st.subheader("Per-fill ledger",
                 help="Every settled fill with: divergence at fill time, fair vs market, won/lost, net P&L. "
                      "Sortable. Surfaced from per_fill_ledger() query.")
    ledger = queries.per_fill_ledger(days_back=14)
    if not ledger.empty:
        st.dataframe(ledger, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
st.title("🌡️ weather_bot · command center")
if show_help:
    with st.expander("ℹ️ How to use this dashboard", expanded=False):
        st.markdown(help_text.OVERVIEW)

tabs = st.tabs(["📊 Status", "📐 Calibration", "💱 Trading", "🔬 Deep Dive"])
with tabs[0]: tab_status()
with tabs[1]: tab_calibration()
with tabs[2]: tab_trading()
with tabs[3]: tab_deep_dive()
