"""Generate an AI-readable context brief for a station/date.

The brief is deterministic context for a human or LLM reviewer. It does not
score trades and does not alter execution.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path

from psycopg.rows import dict_row

from weather_bot.data import persistence


def _fetch(station: str, valid_date: date) -> dict:
    params = {"station": station, "valid_date": valid_date}
    with persistence.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT ticker, lower_f, upper_f, status
              FROM kalshi_market
             WHERE station = %(station)s AND valid_date = %(valid_date)s
             ORDER BY lower_f NULLS FIRST, upper_f NULLS LAST
            """,
            params,
        )
        markets = [dict(r) for r in cur.fetchall()]

        tickers = [m["ticker"] for m in markets]
        signals = []
        if tickers:
            cur.execute(
                """
                SELECT DISTINCT ON (s.ticker)
                       s.ticker, s.ts, s.side, s.fair_prob, s.market_ask, s.market_bid,
                       s.edge, s.action, s.skip_reason, s.notes
                  FROM signal s
                 WHERE s.ticker = ANY(%(tickers)s)
                 ORDER BY s.ticker, s.ts DESC
                """,
                {"tickers": tickers},
            )
            signals = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """
            SELECT percentile, value, run_time
              FROM prob_forecast
             WHERE station = %(station)s AND valid_date = %(valid_date)s AND var = 'TMAX_DAILY'
               AND run_time = (
                   SELECT MAX(run_time)
                     FROM prob_forecast
                    WHERE station = %(station)s AND valid_date = %(valid_date)s AND var = 'TMAX_DAILY'
               )
             ORDER BY percentile
            """,
            params,
        )
        nbm = [dict(r) for r in cur.fetchall()]

        det = []
        for model in ("HRRR", "GFS", "ECMWF"):
            cur.execute(
                """
                SELECT df.model, df.run_time, MAX(df.value)::float AS tmax_f
                  FROM det_forecast df
                  JOIN stations st ON st.code = df.station
                 WHERE df.station = %(station)s
                   AND df.model = %(model)s
                   AND df.var = 'TMP_2M'
                   AND (df.valid_time AT TIME ZONE st.tz)::date = %(valid_date)s
                   AND df.run_time = (
                       SELECT MAX(df2.run_time)
                         FROM det_forecast df2
                         JOIN stations st2 ON st2.code = df2.station
                        WHERE df2.station = %(station)s
                          AND df2.model = %(model)s
                          AND df2.var = 'TMP_2M'
                          AND (df2.valid_time AT TIME ZONE st2.tz)::date = %(valid_date)s
                   )
                 GROUP BY df.model, df.run_time
                """,
                {**params, "model": model},
            )
            row = cur.fetchone()
            if row:
                det.append(dict(row))

        ensemble = []
        for model in ("GFS_ENS", "ECMWF_IFS_ENS", "ECMWF_AIFS_ENS", "WEATHERNEXT2"):
            cur.execute(
                """
                SELECT model, run_time, COUNT(*) AS members,
                       AVG(member_tmax)::float AS mean_tmax_f,
                       STDDEV_POP(member_tmax)::float AS sigma_tmax_f
                  FROM (
                      SELECT ef.model, ef.run_time, ef.member, MAX(ef.value)::float AS member_tmax
                        FROM ensemble_forecast ef
                        JOIN stations st ON st.code = ef.station
                       WHERE ef.station = %(station)s
                         AND ef.model = %(model)s
                         AND ef.var = 'TMP_2M'
                         AND (ef.valid_time AT TIME ZONE st.tz)::date = %(valid_date)s
                         AND ef.run_time = (
                             SELECT MAX(ef2.run_time)
                               FROM ensemble_forecast ef2
                               JOIN stations st2 ON st2.code = ef2.station
                              WHERE ef2.station = %(station)s
                                AND ef2.model = %(model)s
                                AND ef2.var = 'TMP_2M'
                                AND (ef2.valid_time AT TIME ZONE st2.tz)::date = %(valid_date)s
                         )
                       GROUP BY ef.model, ef.run_time, ef.member
                  ) member_daily
                 GROUP BY model, run_time
                """,
                {**params, "model": model},
            )
            row = cur.fetchone()
            if row:
                ensemble.append(dict(row))

        cur.execute(
            """
            SELECT COALESCE(c.tmax_f, d.tmax_f) AS settled_tmax_f,
                   c.tmax_f AS cli_tmax_f,
                   d.tmax_f AS daily_tmax_f,
                   d.source AS daily_source
              FROM (SELECT 1) x
              LEFT JOIN cli_obs c ON c.station = %(station)s AND c.local_date = %(valid_date)s
              LEFT JOIN daily_obs d ON d.station = %(station)s AND d.local_date = %(valid_date)s
            """,
            params,
        )
        obs = dict(cur.fetchone() or {})

        cur.execute(
            """
            SELECT venue, event_slug, market_slug, question, yes_bid, yes_ask, no_bid, no_ask, ts
              FROM external_market_snapshot
             WHERE station = %(station)s AND valid_date = %(valid_date)s
             ORDER BY ts DESC
             LIMIT 20
            """,
            params,
        )
        external = [dict(r) for r in cur.fetchall()]

    return {
        "markets": markets,
        "signals": signals,
        "nbm": nbm,
        "det": det,
        "ensemble": ensemble,
        "obs": obs,
        "external": external,
    }


def _rows(value) -> list[dict]:
    return list(value or [])


def _fmt_price(value) -> str:
    return "-" if value is None else f"{float(value):.3f}"


def render_markdown(station: str, valid_date: date, data: dict) -> str:
    lines = [
        f"# Weather Prediction Context Brief - {station} {valid_date}",
        "",
        f"_generated {datetime.now(tz=timezone.utc):%Y-%m-%d %H:%M UTC}_",
        "",
        "Purpose: provide qualitative context for a human or AI reviewer. This brief is not an execution signal.",
        "",
        "## Settlement / Observation",
        "",
    ]
    obs = data.get("obs") or {}
    lines.append(f"- CLI TMAX: {obs.get('cli_tmax_f')}")
    lines.append(f"- daily_obs TMAX: {obs.get('daily_tmax_f')} ({obs.get('daily_source')})")
    lines.append(f"- settled/preferred TMAX: {obs.get('settled_tmax_f')}")

    lines.extend(["", "## Latest Forecasts", ""])
    nbm = _rows(data.get("nbm"))
    if nbm:
        lines.append("- NBM percentiles: " + ", ".join(f"p{r['percentile']}={float(r['value']):.1f}" for r in nbm))
    for r in _rows(data.get("det"))[:12]:
        lines.append(f"- {r['model']} {r['run_time']}: tmax={float(r['tmax_f']):.1f}")
    for r in _rows(data.get("ensemble")):
        sigma = r.get("sigma_tmax_f")
        sigma_text = "-" if sigma is None else f"{float(sigma):.1f}"
        lines.append(
            f"- {r['model']} {r['run_time']}: members={r['members']} "
            f"mean={float(r['mean_tmax_f']):.1f} sigma={sigma_text}"
        )

    lines.extend(["", "## Kalshi Buckets / Latest Signals", ""])
    signals = {r["ticker"]: r for r in _rows(data.get("signals"))}
    lines.extend(["| ticker | bucket | status | fair | mid | action | skip |", "|---|---|---|---:|---:|---|---|"])
    for market in _rows(data.get("markets")):
        sig = signals.get(market["ticker"], {})
        ask = sig.get("market_ask")
        bid = sig.get("market_bid")
        mid = None if ask is None or bid is None else (float(ask) + float(bid)) / 2.0
        bucket = f"{market.get('lower_f')} to {market.get('upper_f')}"
        lines.append(
            f"| {market['ticker']} | {bucket} | {market.get('status')} | "
            f"{_fmt_price(sig.get('fair_prob'))} | {_fmt_price(mid)} | "
            f"{sig.get('action', '-')} | {sig.get('skip_reason') or '-'} |"
        )

    external = _rows(data.get("external"))
    if external:
        lines.extend(["", "## External Market Snapshots", ""])
        lines.extend(["| venue | question | yes bid | yes ask | ts |", "|---|---|---:|---:|---|"])
        for r in external:
            lines.append(
                f"| {r['venue']} | {r['question']} | {_fmt_price(r.get('yes_bid'))} | "
                f"{_fmt_price(r.get('yes_ask'))} | {r['ts']} |"
            )

    lines.extend([
        "",
        "## AI Review Guardrails",
        "",
        "- Look for context the numeric model may miss: settlement wording, station mismatch, stale markets, forecast run jumps, obs-vs-forecast contradictions, and boundary risk.",
        "- Do not recommend an order unless the deterministic bot already shows positive fee-aware EV.",
        "- Output should be advisory only: `context_supports`, `context_warns`, or `insufficient_context`.",
    ])
    return "\n".join(lines) + "\n"


def run(station: str, valid_date: date, out_dir: Path = Path("research/reports")) -> dict:
    station = station.upper()
    out_dir.mkdir(parents=True, exist_ok=True)
    data = _fetch(station, valid_date)
    text = render_markdown(station, valid_date, data)
    path = out_dir / f"ai_context_{station}_{valid_date}.md"
    path.write_text(text)
    return {"report_path": str(path), "text": text}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", required=True)
    parser.add_argument("--valid-date", type=date.fromisoformat, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("research/reports"))
    args = parser.parse_args()
    result = run(args.station, args.valid_date, args.out_dir)
    print(result["text"])
