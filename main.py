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
from datetime import datetime, timezone

from weather_bot.config import ACTIVE_STATIONS, BANKROLL_USD, PAPER_MODE
from weather_bot.data import persistence
from weather_bot.data.persistence import connect
from weather_bot.models.distribution import build_station_distribution
from weather_bot.strategy import ev
from weather_bot.strategy.kalshi_client import KalshiClient

log = logging.getLogger(__name__)


def _load_open_markets() -> list[dict]:
    sql = """
    SELECT ticker, station, var, valid_date, lower_f, upper_f
      FROM kalshi_market
     WHERE status IN ('open', 'active')
       AND station = ANY(%s)
       AND valid_date >= CURRENT_DATE
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (ACTIVE_STATIONS,))
        return cur.fetchall()


def _best_price(entries, cents: bool) -> float | None:
    """Return the highest (best) bid price in dollars from a list of [price, qty] rows.

    `cents=True`  → legacy shape: integer cents (e.g. [[97, 100], ...])
    `cents=False` → modern shape: dollar strings  (e.g. [["0.9700", "100.00"], ...])
    Always scans all entries — we don't trust the sort order across API versions.
    """
    best: float | None = None
    for e in entries or []:
        try:
            p = float(e[0])
        except (IndexError, ValueError, TypeError):
            continue
        if cents:
            p = p / 100.0
        if best is None or p > best:
            best = p
    return best


def _load_orderbook_top(client: KalshiClient, ticker: str) -> tuple[float | None, float | None]:
    """Return (yes_ask, yes_bid) in dollars.

    Kalshi payload evolved — modern response carries `orderbook_fp` with
    `yes_dollars` / `no_dollars` as [[price_str, qty_str], ...]. Legacy
    response used `orderbook.yes|no` with integer cents. Handle both.
    """
    try:
        ob = client.get_orderbook(ticker)
    except Exception as exc:
        log.warning("orderbook failed for %s: %s", ticker, exc)
        return (None, None)

    fp = ob.get("orderbook_fp") or {}
    if fp:
        yes_bid = _best_price(fp.get("yes_dollars"), cents=False)
        no_bid = _best_price(fp.get("no_dollars"), cents=False)
    else:
        legacy = ob.get("orderbook") or {}
        yes_bid = _best_price(legacy.get("yes"), cents=True)
        no_bid = _best_price(legacy.get("no"), cents=True)

    # YES ask = 1 - best NO bid (selling NO is equivalent to buying YES).
    yes_ask = (1.0 - no_bid) if no_bid is not None else None
    return yes_ask, yes_bid


def run():
    persistence.bootstrap_stations()
    client = KalshiClient()
    markets = _load_open_markets()
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

        fair_prob = cdf.prob_between(m["lower_f"], m["upper_f"])
        yes_ask, yes_bid = _load_orderbook_top(client, m["ticker"])

        sig = ev.evaluate(m["ticker"], fair_prob, yes_ask, yes_bid, bankroll=BANKROLL_USD)

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
                fair_nb = cdf_nb.prob_between(m["lower_f"], m["upper_f"])
                sig_nb = ev.evaluate(m["ticker"], fair_nb, yes_ask, yes_bid, bankroll=BANKROLL_USD)
                if sig_nb.action == "OPEN":
                    sig_nb.notes = f"BIAS_BLAMED|fair_biased={fair_prob:.3f}|fair_no_bias={fair_nb:.3f} {sig_nb.notes}"
                    log.warning(
                        "BIAS_BLAMED %s: bias-on fair=%.3f tripped divergence; bias-off fair=%.3f trades %s",
                        m["ticker"], fair_prob, fair_nb, sig_nb.side,
                    )
                    sig, fair_prob = sig_nb, fair_nb
                elif sig_nb.skip_reason == "DIVERGENCE":
                    sig.notes = f"MODEL_BLAMED|fair_no_bias={fair_nb:.3f} {sig.notes}"
                else:
                    sig.notes = f"BIAS_BLAMED_NO_EDGE|fair_no_bias={fair_nb:.3f}|nb_skip={sig_nb.skip_reason} {sig.notes}"

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
        ))

        # Paper-fill writer — only when action=OPEN and no existing open fill.
        if (
            PAPER_MODE
            and sig.action == "OPEN"
            and sig.size_usd >= 1.0
            and not persistence.has_open_paper_fill(sig.ticker, sig.side)
        ):
            # Fill price: for YES side, pay yes_ask; for NO side, pay (1 - yes_bid).
            fill_price = (
                sig.market_ask if sig.side == "YES" else (1.0 - sig.market_bid)
            )
            if fill_price is None or fill_price <= 0 or fill_price >= 1:
                continue
            contracts = max(1, int(sig.size_usd / fill_price))
            fees = ev.fee_per_contract(fill_price) * contracts
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
