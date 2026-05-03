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

st.set_page_config(page_title="weather_bot · Command Center", layout="wide", page_icon="🌡️")

# Tab dispatch lives in the sidebar (st.radio) instead of st.tabs because
# tabs eagerly evaluate every panel on every refresh — visible cost on a
# 15s auto-refresh. Radio renders only the selected tab.
TAB_ORDER = ["📊 Status", "📐 Calibration", "💱 Trading", "🔬 Deep Dive"]

with st.sidebar:
    st.title("⚙️ Controls")
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
    show_help = st.toggle("Show help panels", value=True,
                          help="Expand legend / explanation text on each tab.")
    st.divider()
    st.caption("**Trading:** " + ", ".join(queries.trade_eligible_stations()))
    st.caption("**Gathering data only:** " + ", ".join(s for s in queries.fetch_stations()
                                              if s not in queries.trade_eligible_stations()))
    st.divider()
    with st.expander("📖 Glossary — What These Terms Mean"):
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

    # Edge-gap line chart with threshold band
    st.subheader("Daily Expected vs Realized Edge",
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
    st.subheader("Reliability Diagram (Last 30 Days)",
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
                      "latest run for each valid_date. HRRR/GFS use max(hourly TMP_2M) "
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
                      color_discrete_map={"NBM": "#2563eb", "HRRR": "#f59e0b", "GFS": "#16a34a"})
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
            with st.expander(f"📋 Forecast audit log ({st_code} today)", expanded=False):
                audit = queries.forecast_audit_log(st_code, today)
                if audit.empty:
                    st.caption("No forecasts logged for today yet.")
                else:
                    st.caption(f"{len(audit)} forecast issuances chronologically (NBM p50, HRRR daily-MAX, GFS daily-MAX)")
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
            rows_html.append(
                f"<tr>"
                f"<td>{r['ticker']}</td><td>{r['side']}</td>"
                f"<td>{r['price']:.2f}</td><td>{r['contracts']}</td>"
                f"<td>{r['valid_date']}</td><td>{r['days_to_settle']}d</td>"
                f"<td>{obs_str}</td><td>{p50_str}</td><td>{mtm_str}</td>"
                f"<td title='{state_explain}' style='color:{state_color};font-weight:600'>{state_label}</td>"
                f"</tr>"
            )
        header = ("<table style='width:100%;font-size:0.85em;border-collapse:collapse'>"
                  "<thead><tr style='border-bottom:1px solid #444'>"
                  "<th>ticker</th><th>side</th><th>price</th><th>contracts</th>"
                  "<th>valid_date</th><th>days</th>"
                  "<th>obs so far</th><th>p50</th><th>MtM</th><th>settlement</th>"
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
                      "BIAS_GATE = bias table missing/thin/stale; TRIPWIRE_RED = health-check block.")
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

        st.dataframe(sigs_f[["ts", "ticker", "station", "var", "side", "fair_prob",
                              "market_ask", "market_bid", "divergence", "edge", "size_usd",
                              "action", "skip_reason", "votes", "risk", "notes"]],
                      use_container_width=True, hide_index=True)

    # Distribution preview
    st.subheader("Distribution Preview (Today)",
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
st.title("🌡️ weather_bot · Command Center")
if show_help:
    with st.expander("ℹ️ How to Use This Dashboard", expanded=False):
        st.markdown(help_text.OVERVIEW)

# Single-tab render based on sidebar radio selection (no st.tabs eager eval).
TAB_DISPATCH = {
    "📊 Status":      tab_status,
    "📐 Calibration": tab_calibration,
    "💱 Trading":     tab_trading,
    "🔬 Deep Dive":   tab_deep_dive,
}
TAB_DISPATCH[selected_tab]()
