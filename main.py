"""
Signal orchestrator.

For each open Kalshi weather market:
  1. Load the relevant station distribution (NBM + bias + HRRR blend).
  2. Integrate the distribution across the market's bucket to get fair_prob.
  3. Fetch top-of-book from Kalshi.
  4. Compute edge / EV / sizing via strategy.ev.evaluate().
  5. Log signal to DB (paper mode — no orders sent).

Run this every 10-15 minutes during trading hours once NBM/HRRR/METAR jobs
are producing fresh data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from weather_bot.config import (
    ACTIVE_TRADE_STATIONS,
    BANKROLL_USD,
    PAPER_BYPASS_TRIPWIRE,
    PAPER_ORDER_IMPROVEMENT_CENTS,
    PAPER_ORDER_MODE,
    PAPER_ORDER_TTL_MIN,
    PAPER_MODE,
    REQUIRE_AGREEMENT_N,
    REQUIRE_TOP_BOOK_SIZE,
    TAKE_PROFIT_THRESHOLD,
)
from weather_bot.data import persistence
from weather_bot.data.persistence import connect
from weather_bot.models.bias_correction import is_station_calibrated
from weather_bot.models.distribution import build_station_distribution, lead_day_for_station
from weather_bot.strategy import ev, paper_orders, profitability, reversal_risk
from weather_bot.strategy.kalshi_client import KalshiClient
from weather_bot.strategy.probability_calibration import calibrate_fair_probability

# Max lead_day allowed for live trading. Set to 0 (same-day only) after
# 2026-05-24 PnL audit found lead>=1 cells lost -$491 across all stations
# while lead=0 KNYC YES carried +$482 — the only proven edge is same-day.
MAX_LEAD_DAY_TO_TRADE = 0

# Minimum NO contract price ($0.50). NO bets below this fade a strong YES
# market (yes_bid > $0.50). 2026-05-24 audit: lead=0 NO bets at price <$0.50
# went 0/6 for -$124. NO bets at price >=$0.50 were +$50 net across 14 fills.
NO_PRICE_FLOOR = 0.50


def _vote_for_bucket(point_est: float | None, lower_f: float | None, upper_f: float | None) -> str:
    """Map a point-estimate temp to a directional vote on a Kalshi range bucket."""
    if point_est is None:
        return "NA"
    lo = lower_f if lower_f is not None else float("-inf")
    hi = upper_f if upper_f is not None else float("inf")
    return "YES" if lo <= point_est < hi else "NO"


def _compute_model_votes(station: str, valid_date, var: str,
                          lower_f: float | None, upper_f: float | None) -> dict:
    """Per-model directional vote (NBM p50, HRRR daily MAX, GFS daily MAX) on
    the given Kalshi bucket. TMAX_DAILY only — HRRR/GFS aren't naturally daily
    minima, so TMIN markets just see NBM in the votes."""
    points: dict[str, float] = {}
    nbm_rows = persistence.latest_nbm_percentiles(station, valid_date, var)
    for r in nbm_rows:
        if r["percentile"] == 50:
            points["NBM"] = float(r["value"])
            break
    if var == "TMAX_DAILY":
        if (v := persistence.latest_hrrr_tmax(station, valid_date)) is not None:
            points["HRRR"] = float(v)
        if (v := persistence.latest_gfs_tmax(station, valid_date)) is not None:
            points["GFS"] = float(v)
    votes = {m: _vote_for_bucket(p, lower_f, upper_f) for m, p in points.items()}
    n_yes = sum(1 for v in votes.values() if v == "YES")
    n_no = sum(1 for v in votes.values() if v == "NO")
    return {**votes, "n_yes": n_yes, "n_no": n_no, "n_total": n_yes + n_no}


def _tripwire_red_stations() -> set[str]:
    """Stations currently flagged RED by the health-check tripwire.

    main.py refuses to open new positions on these stations until a human
    acknowledges the alert (sets acknowledged_at on the latest row).
    """
    sql = """
    SELECT DISTINCT station FROM health_check_latest
     WHERE status = 'RED' AND acknowledged_at IS NULL AND station != 'GLOBAL'
    """
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return {r["station"] for r in cur.fetchall()}
    except Exception as exc:
        # Health table may not exist yet on first deploy. Fail open: never
        # let a missing tripwire table BLOCK trading. Logged so it's visible.
        log.warning("tripwire query failed (failing open): %s", exc)
        return set()

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrderbookTop:
    yes_ask: float | None
    yes_bid: float | None
    yes_ask_size: int | None
    yes_bid_size: int | None


def _load_open_markets() -> list[dict]:
    sql = """
    SELECT ticker, station, var, valid_date, lower_f, upper_f
      FROM kalshi_market
     WHERE status IN ('open', 'active')
       AND station = ANY(%s)
       AND valid_date >= CURRENT_DATE
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (ACTIVE_TRADE_STATIONS,))
        return cur.fetchall()


def _best_bid(entries, cents: bool) -> tuple[float | None, int | None]:
    """Return highest bid price and size from a list of [price, qty] rows.

    `cents=True`  → legacy shape: integer cents (e.g. [[97, 100], ...])
    `cents=False` → modern shape: dollar strings  (e.g. [["0.9700", "100.00"], ...])
    Always scans all entries — we don't trust the sort order across API versions.
    """
    best: float | None = None
    best_size: int | None = None
    for e in entries or []:
        try:
            p = float(e[0])
            size = int(float(e[1])) if len(e) > 1 and e[1] is not None else None
        except (IndexError, ValueError, TypeError):
            continue
        if cents:
            p = p / 100.0
        if best is None or p > best:
            best = p
            best_size = size
    return best, best_size


def _load_orderbook_top(client: KalshiClient, ticker: str) -> OrderbookTop:
    """Return top-of-book prices and executable sizes in dollars/contracts.

    Kalshi payload evolved — modern response carries `orderbook_fp` with
    `yes_dollars` / `no_dollars` as [[price_str, qty_str], ...]. Legacy
    response used `orderbook.yes|no` with integer cents. Handle both.
    """
    try:
        ob = client.get_orderbook(ticker)
    except Exception as exc:
        log.warning("orderbook failed for %s: %s", ticker, exc)
        return OrderbookTop(None, None, None, None)

    fp = ob.get("orderbook_fp") or {}
    if fp:
        yes_bid, yes_bid_size = _best_bid(fp.get("yes_dollars"), cents=False)
        no_bid, no_bid_size = _best_bid(fp.get("no_dollars"), cents=False)
    else:
        legacy = ob.get("orderbook") or {}
        yes_bid, yes_bid_size = _best_bid(legacy.get("yes"), cents=True)
        no_bid, no_bid_size = _best_bid(legacy.get("no"), cents=True)

    # YES ask = 1 - best NO bid (selling NO is equivalent to buying YES).
    yes_ask = (1.0 - no_bid) if no_bid is not None else None
    # YES ask size is limited by the best NO bid size.
    return OrderbookTop(yes_ask, yes_bid, no_bid_size, yes_bid_size)


def _ask_size_for_side(sig, top: OrderbookTop) -> int | None:
    if sig.side == "YES":
        return top.yes_ask_size
    return top.yes_bid_size


def run():
    persistence.bootstrap_stations()
    if PAPER_MODE:
        from weather_bot.strategy import early_exits
        exit_summary = early_exits.process_early_exits(threshold=TAKE_PROFIT_THRESHOLD)
        if exit_summary.checked or exit_summary.exited:
            log.info(
                "Processed early exits: checked=%d exited=%d "
                "(skipped_stale=%d skipped_thin_book=%d)",
                exit_summary.checked,
                exit_summary.exited,
                exit_summary.skipped_stale,
                exit_summary.skipped_thin_book,
            )
    if PAPER_MODE and PAPER_ORDER_MODE:
        summary = paper_orders.process_pending_orders()
        if summary.checked or summary.filled or summary.expired:
            log.info(
                "Processed pending paper orders: checked=%d filled=%d expired=%d",
                summary.checked,
                summary.filled,
                summary.expired,
            )
    client = KalshiClient()
    markets = _load_open_markets()
    tripwire_bypassed = PAPER_MODE and PAPER_BYPASS_TRIPWIRE
    red_stations: set[str] = set() if tripwire_bypassed else _tripwire_red_stations()
    if tripwire_bypassed:
        log.info("TRIPWIRE bypass active (paper mode); RED stations will still trade for sample velocity")
    elif red_stations:
        log.warning("TRIPWIRE: stations flagged RED — refusing new positions: %s", sorted(red_stations))
    log.info("Evaluating %d open markets (paper_mode=%s)", len(markets), PAPER_MODE)

    cache: dict[tuple[str, str, str], object] = {}
    cache_no_bias: dict[tuple[str, str, str], object] = {}
    now_utc = datetime.now(tz=timezone.utc)

    for m in markets:
        key = (m["station"], str(m["valid_date"]), m["var"])
        if key not in cache:
            cache[key] = build_station_distribution(m["station"], m["valid_date"], m["var"], now_utc=now_utc)
        cdf = cache[key]
        if cdf is None:
            log.debug("Skipping %s — no distribution", m["ticker"])
            continue

        lead_day = max(0, lead_day_for_station(m["station"], m["valid_date"], now_utc))
        raw_fair_prob = cdf.prob_between(m["lower_f"], m["upper_f"])
        cal = calibrate_fair_probability(m["station"], raw_fair_prob, lead_day=lead_day)
        fair_prob = cal.calibrated_prob
        top = _load_orderbook_top(client, m["ticker"])
        yes_ask, yes_bid = top.yes_ask, top.yes_bid

        sig = ev.evaluate(m["ticker"], fair_prob, yes_ask, yes_bid, bankroll=BANKROLL_USD)
        if cal.applied:
            sig.notes = f"{cal.note()} {sig.notes}"

        # Multi-model directional vote — recorded on every signal regardless of
        # action, so the dashboard can show "what did each model think?" even
        # for SKIPs. The agreement gate below uses these to optionally block
        # OPEN signals when models disagree.
        sig.model_votes = _compute_model_votes(
            m["station"], m["valid_date"], m["var"], m["lower_f"], m["upper_f"]
        )

        # Composite reversal-risk score (Sprint 3) — diagnostic-only on every
        # signal. Combines model spread, fair-vs-market gap, boundary mass,
        # time remaining, NWS overnight jump, regional gradient, and recent
        # rate-of-change. Intentionally not used to gate or size yet — needs
        # a backtest like we did for the agreement gate before relying on it.
        try:
            yes_mid = ((yes_ask + yes_bid) / 2.0) if (yes_ask is not None and yes_bid is not None) else None
            mkt_mid = yes_mid if sig.side == "YES" else (1.0 - yes_mid if yes_mid is not None else None)
            rr = reversal_risk.compute(
                station=m["station"], valid_date=m["valid_date"],
                lower_f=m["lower_f"], upper_f=m["upper_f"],
                fair_prob=fair_prob, market_mid=mkt_mid, cdf=cdf,
            )
            sig.reversal_risk = rr.to_jsonb()
        except Exception as exc:
            log.warning("reversal_risk compute failed for %s: %s", m["ticker"], exc)
            sig.reversal_risk = None

        # Agreement gate (config flag REQUIRE_AGREEMENT_N, default 0 = disabled).
        # When enabled and the bot wants to OPEN, require N models to vote with
        # the bot's chosen side (YES or NO).
        if (sig.action == "OPEN" and REQUIRE_AGREEMENT_N > 0
                and sig.model_votes["n_total"] >= 2):
            same_side = sig.model_votes["n_yes"] if sig.side == "YES" else sig.model_votes["n_no"]
            if same_side < REQUIRE_AGREEMENT_N:
                sig.action = "SKIP"
                sig.skip_reason = "AGREEMENT"
                sig.notes = (f"AGREEMENT|need={REQUIRE_AGREEMENT_N}|same_side={same_side}|"
                              f"votes={sig.model_votes} {sig.notes}")

        # Pre-trade safety gates: tripwire (calibration drift) + bias staleness.
        # These short-circuit OPEN→SKIP without touching the model. The original
        # signal still gets logged so the dashboard can surface "would have
        # opened but for X" for diagnostic purposes.
        if sig.action == "OPEN" and m["station"] in red_stations:
            sig.action = "SKIP"
            sig.skip_reason = "TRIPWIRE_RED"
            sig.notes = f"TRIPWIRE_RED|station={m['station']} {sig.notes}"
        if sig.action == "OPEN":
            lead_day = lead_day_for_station(m["station"], m["valid_date"], now_utc)
            eligible, reason = is_station_calibrated(m["station"], m["var"], m["valid_date"], lead_day)
            if not eligible:
                sig.action = "SKIP"
                sig.skip_reason = "BIAS_GATE"
                sig.notes = f"BIAS_GATE|{reason} {sig.notes}"

        if sig.action == "OPEN" and lead_day > MAX_LEAD_DAY_TO_TRADE:
            sig.action = "SKIP"
            sig.skip_reason = "LEAD_DAY_GATE"
            sig.notes = f"LEAD_DAY_GATE|lead={lead_day}>max={MAX_LEAD_DAY_TO_TRADE} {sig.notes}"

        if sig.action == "OPEN" and sig.side == "NO" and sig.market_bid is not None:
            no_price = 1.0 - float(sig.market_bid)
            if no_price < NO_PRICE_FLOOR:
                sig.action = "SKIP"
                sig.skip_reason = "NO_FADE_GATE"
                sig.notes = f"NO_FADE_GATE|no_price={no_price:.3f}<{NO_PRICE_FLOOR} {sig.notes}"

        # Divergence bypass: when bias-corrected fair disagrees sharply with the
        # market, ask whether the bias table is the source of disagreement by
        # rebuilding the distribution without bias correction and re-evaluating.
        # Three outcomes:
        #   - no-bias signal becomes OPEN     → BIAS_BLAMED, trade the no-bias signal
        #   - no-bias signal still DIVERGENCE → MODEL_BLAMED, skip (current behavior)
        #   - no-bias signal SKIP for other reason → BIAS_BLAMED_NO_EDGE, skip
        if sig.skip_reason == "DIVERGENCE":
            if key not in cache_no_bias:
                cache_no_bias[key] = build_station_distribution(
                    m["station"], m["valid_date"], m["var"], now_utc=now_utc, apply_bias=False
                )
            cdf_nb = cache_no_bias[key]
            if cdf_nb is not None:
                raw_fair_nb = cdf_nb.prob_between(m["lower_f"], m["upper_f"])
                cal_nb = calibrate_fair_probability(m["station"], raw_fair_nb, lead_day=lead_day)
                fair_nb = cal_nb.calibrated_prob
                sig_nb = ev.evaluate(m["ticker"], fair_nb, yes_ask, yes_bid, bankroll=BANKROLL_USD)
                if cal_nb.applied:
                    sig_nb.notes = f"{cal_nb.note()} {sig_nb.notes}"
                if sig_nb.action == "OPEN":
                    sig_nb.notes = (
                        f"BIAS_BLAMED|fair_biased={fair_prob:.3f}|"
                        f"fair_no_bias={fair_nb:.3f}|raw_no_bias={raw_fair_nb:.3f} "
                        f"{sig_nb.notes}"
                    )
                    log.warning(
                        "BIAS_BLAMED %s: bias-on fair=%.3f tripped divergence; bias-off fair=%.3f trades %s",
                        m["ticker"], fair_prob, fair_nb, sig_nb.side,
                    )
                    # Preserve diagnostics computed earlier on the original sig.
                    # They describe the same (station, valid_date, bucket) so they
                    # remain valid when we swap to the no-bias signal.
                    sig_nb.model_votes = sig.model_votes
                    sig_nb.reversal_risk = sig.reversal_risk
                    sig, fair_prob = sig_nb, fair_nb
                elif sig_nb.skip_reason == "DIVERGENCE":
                    sig.notes = f"MODEL_BLAMED|fair_no_bias={fair_nb:.3f}|raw_no_bias={raw_fair_nb:.3f} {sig.notes}"
                else:
                    sig.notes = f"BIAS_BLAMED_NO_EDGE|fair_no_bias={fair_nb:.3f}|raw_no_bias={raw_fair_nb:.3f}|nb_skip={sig_nb.skip_reason} {sig.notes}"

        # Intraday-squeeze guardrail: same-day TMAX/TMIN markets where the
        # intraday floor/ceiling already sits inside the bucket. For YES the
        # win window has been squeezed to (upper - floor) or (ceiling - lower).
        # When that window is smaller than a normal remaining-day move, the
        # bot's renormalized fair probability is fragile and tends to disagree
        # with the market (which prices in the obvious "temp will keep climbing"
        # path). Symmetric block for TMIN ceiling.
        # The check is YES-only; NO benefits from a tight remaining window
        # because just a small further move past the bucket edge resolves NO.
        _INTRADAY_SQUEEZE_MIN_F = 1.5
        if sig.action == "OPEN" and sig.side == "YES" and lead_day == 0:
            squeeze_room: float | None = None
            squeeze_label: str | None = None
            if m["var"] == "TMAX_DAILY" and cdf.floor is not None and m["upper_f"] is not None:
                squeeze_room = float(m["upper_f"]) - float(cdf.floor)
                squeeze_label = f"TMAX|floor={cdf.floor:.1f}|upper={m['upper_f']:.1f}"
            elif m["var"] == "TMIN_DAILY" and cdf.ceiling is not None and m["lower_f"] is not None:
                squeeze_room = float(cdf.ceiling) - float(m["lower_f"])
                squeeze_label = f"TMIN|ceiling={cdf.ceiling:.1f}|lower={m['lower_f']:.1f}"
            if squeeze_room is not None and squeeze_room < _INTRADAY_SQUEEZE_MIN_F:
                sig.action = "SKIP"
                sig.skip_reason = "INTRADAY_SQUEEZE"
                sig.notes = (
                    f"INTRADAY_SQUEEZE|room={squeeze_room:.2f}F<{_INTRADAY_SQUEEZE_MIN_F:.1f}F|"
                    f"{squeeze_label} {sig.notes}"
                )

        sig = profitability.apply_profitability_controls(
            sig, m["station"], m["valid_date"], now_utc
        )

        log.info(
            "%-32s %s fair=%.3f ask=%s bid=%s action=%s edge=%.4f size=$%.2f (%s)",
            m["ticker"], m["var"], fair_prob,
            f"{yes_ask:.3f}" if yes_ask else "—",
            f"{yes_bid:.3f}" if yes_bid else "—",
            sig.action, sig.edge, sig.size_usd, sig.notes,
        )

        signal_id = persistence.insert_signal(dict(
            ticker=sig.ticker,
            side=sig.side,
            fair_prob=sig.fair_prob,
            market_ask=sig.market_ask,
            market_bid=sig.market_bid,
            edge=sig.edge,
            ev_per_dollar=sig.ev_per_dollar,
            kelly_fraction=sig.kelly_fraction,
            size_usd=sig.size_usd,
            action=sig.action,
            notes=sig.notes,
            skip_reason=sig.skip_reason,
            model_votes=sig.model_votes,
            reversal_risk=sig.reversal_risk,
        ))

        # Paper execution writer — maker-style pending orders by default, with
        # immediate fills still available behind PAPER_ORDER_MODE=false.
        if (
            PAPER_MODE
            and sig.action == "OPEN"
            and sig.size_usd >= 1.0
            and not persistence.has_open_paper_fill(sig.ticker, sig.side)
            and not persistence.has_pending_paper_order(sig.ticker, sig.side)
        ):
            entry_price = paper_orders.entry_price_from_signal(sig)
            if entry_price is None or entry_price <= 0 or entry_price >= 1:
                continue

            if PAPER_ORDER_MODE:
                limit_price = paper_orders.maker_limit_price(entry_price, PAPER_ORDER_IMPROVEMENT_CENTS)
                if limit_price <= 0 or limit_price >= 1:
                    log.info("  PAPER ORDER SKIP %s %s — invalid limit %.3f", sig.ticker, sig.side, limit_price)
                    continue
                contracts = max(1, int(sig.size_usd / limit_price))
                fees_est = ev.fee_for_order(limit_price, contracts)
                expires_at = now_utc + timedelta(minutes=PAPER_ORDER_TTL_MIN)
                order_id = persistence.insert_paper_order(dict(
                    signal_id=signal_id,
                    ticker=sig.ticker,
                    side=sig.side,
                    limit_price=float(limit_price),
                    contracts=int(contracts),
                    fees_est=float(fees_est),
                    expires_at=expires_at,
                    notes=(
                        f"PAPER_MAKER|from={entry_price:.2f}|improve_c={PAPER_ORDER_IMPROVEMENT_CENTS}|"
                        f"ttl_min={PAPER_ORDER_TTL_MIN}"
                    ),
                ))
                log.info(
                    "  PAPER ORDER %s %s limit=%.2f x%d ttl=%dm (fees_est=$%.2f, id=%d)",
                    sig.ticker, sig.side, limit_price, contracts, PAPER_ORDER_TTL_MIN, fees_est, order_id,
                )
            else:
                fill_price = entry_price
                contracts = max(1, int(sig.size_usd / fill_price))
                fees = ev.fee_for_order(fill_price, contracts)
                ask_size = _ask_size_for_side(sig, top)
                if REQUIRE_TOP_BOOK_SIZE and ask_size is None:
                    log.info("  PAPER FILL SKIP %s %s — no top-book size", sig.ticker, sig.side)
                    continue
                if ask_size is not None and contracts > ask_size:
                    if REQUIRE_TOP_BOOK_SIZE:
                        contracts = int(ask_size)
                        if contracts <= 0:
                            log.info("  PAPER FILL SKIP %s %s — top-book size is zero", sig.ticker, sig.side)
                            continue
                        fees = ev.fee_for_order(fill_price, contracts)
                        sig.notes = f"EXEC_SIZE_CAP|top_book={ask_size} {sig.notes}"
                    else:
                        log.info(
                            "  PAPER FILL %s %s requested x%d exceeds top-book x%d",
                            sig.ticker, sig.side, contracts, ask_size,
                        )
                persistence.insert_paper_fill(dict(
                    signal_id=signal_id,
                    ticker=sig.ticker,
                    side=sig.side,
                    price=float(fill_price),
                    contracts=int(contracts),
                    fees=float(fees),
                ))
                log.info(
                    "  PAPER FILL %s %s @%.3f x%d (fees=$%.2f, notional=$%.2f)",
                    sig.ticker, sig.side, fill_price, contracts, fees, contracts * fill_price,
                )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run()
