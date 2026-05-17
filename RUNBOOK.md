# Weather Bot Runbook — Current Operating Instructions

Single source of truth. Replaces `NEXT_STEPS.md`, `IMPLEMENTATION_SUMMARY.md`,
and `ANALYSIS_2026-05-06.md` (archived under `docs/history/`).

Last reviewed: 2026-05-17.

---

## 1. Operating posture

- `PAPER_MODE=true` is the right default and stays on.
- In paper mode, `PAPER_BYPASS_TRIPWIRE=true` and `PAPER_BYPASS_STATION_PAUSE=true`
  are also default-on. Their purpose is to keep generating settled-fill samples
  so calibration changes can actually be measured. **Live mode** flips both off
  (set `PAPER_MODE=false` and these bypasses are ignored).
- `BIAS_GATE` and `DIVERGENCE` remain active in paper — they prevent
  meaningless or upstream-broken signals from polluting calibration.

## 2. Current methodology

### 2.1 Distribution model (models/distribution.py)

Piecewise CDF from NBM QMD percentiles → station bias shrinkage →
lead-aware variance inflation → HRRR same-day blend → GFS multi-day blend →
intraday floor/ceiling.

Lead-aware spread schedule (replaces the retracted 2026-05-06 blanket `1.35x`):

| Lead day | Variance multiplier | Max widening cap |
|---:|---:|---:|
| 0 | 1.00 | 1.10 |
| 1 | 1.25 | 1.35 |
| 2 | 1.15 | 1.25 |
| 3+ | 1.05 | 1.15 |

L0 is unchanged because HRRR blending and intraday floor/ceiling already
dominate same-day uncertainty.

Bias shrinkage: empirical-Bayes `lambda = n / (n + 10)`, zeroed when
`|raw_bias| < SE(mean)`, staleness-tapered linearly between 8h and 18h
cycle age.

HRRR blend curve (same-day TMAX): `06h→0.2, 10h→0.6, 15h→0.9, 18h+→0.95`.

GFS blend: constant `0.30` weight for lead≥1 and same-day fallback when
HRRR is unavailable. GFS consistently beats NBM at all stations.

### 2.2 Calibration rules

- **Side-adjusted fair probability**: `YES → fair_prob`, `NO → 1 - fair_prob`.
  Never use trade `price` as a proxy.
- **`paper_fill.payout > 0` means the fill won**, regardless of YES/NO.
- **`lead_day_for_station(station, valid_date, now_utc)`** — never use bare
  UTC dates.
- **`fee_for_order(price, contracts)`** — Kalshi rounds at order level. Do
  not multiply a one-contract fee by contract count.
- **Empirical probability calibrator** (signal-based, event-weighted).
  Fallback ladder: `station+lead+bucket → lead+bucket → station+bucket → global`.
  Repeated scores for the same ticker/bin contribute one effective event.
  Conservative defaults while samples are thin:
  `PROB_CALIBRATION_MIN_BUCKET_N=20`, `PROB_CALIBRATION_PRIOR_N=35`,
  `PROB_CALIBRATION_MAX_DELTA=0.15`.

### 2.3 Profitability controls (strategy/profitability.py)

| Control | Default | Rationale |
|---|---|---|
| `PAUSED_TRADE_STATIONS=KMDW` | Paused live, bypassed in paper | KMDW thin; need samples |
| `KNYC_L1_SIZE_MULT=0.25` | Quarter-size | L1 overconfident `+0.16` |
| `NO_UNDER_50C_SIZE_MULT=0.0` | Block | Persistent loser |
| `YES_UNDER_10C_SIZE_MULT=0.0` | Block | Persistent loser (`−$178` over 34 fills) |
| `YES_10_25C_SIZE_MULT=0.50` + `YES_10_25C_MAX_USD=25.0` | Half-size, capped | Only positive low-price sleeve (`+$59` over 30 fills). Cap raised from `$10` to `$25` on 2026-05-17 to lift sample-velocity throttle. |
| `YES_25_50C_SIZE_MULT=0.50` | Half-size | Weak band |
| `REQUIRE_TOP_BOOK_SIZE=true` | Cap fills to top-of-book | Avoid stale executable-price assumptions |
| `PAPER_ORDER_MODE=true` | Maker-style pending orders | Reduces stale-snapshot fills; default improvement +1¢, TTL 15m |

### 2.4 Autonomy guardrails

Refuses new positions when (any):

1. **`BIAS_GATE`** — `(station, var, month, lead_day)` cell missing, `n<10`, or `>48h` stale.
2. **`TRIPWIRE_RED`** — health_check has the station flagged RED on
   MODEL/RISK/PNL, no human ack. **Bypassed in paper mode.**
3. **`DIVERGENCE`** — `|fair − market_mid| > 0.50`. Has a bias-blamed
   fallback path that re-evaluates without bias correction.
4. **`PROFIT_GATE`** — paused stations or sub-minimum sizing.

## 3. Current calibration baseline (Apr 1 – May 6, 2026)

Side-adjusted, corrected fees:

| Lead | Fills | Predicted win | Observed win | Error |
|---|---:|---:|---:|---:|
| L0 | 33 | 0.500 | 0.394 | +0.106 |
| L1 | 96 | 0.700 | 0.542 | +0.159 |

L1 by station:

| Station | Fills | Error |
|---|---:|---:|
| KNYC | 73 | +0.164 |
| KMIA | 18 | +0.098 |
| KMDW | 5  | +0.308 |

L1 is still overconfident; smaller than the original price-proxy diagnosis.

## 4. Validation commands

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m compileall -q main.py strategy models data dashboard jobs research verification tests
.venv/bin/python research/profile_calibration.py --start-date 2026-04-01 --end-date 2026-05-06
.venv/bin/python research/backtest_variance_fix.py --start 2026-04-01 --end 2026-05-06
.venv/bin/python research/monitor_edge_accuracy.py --hours 1000
.venv/bin/python -m weather_bot.jobs.profitability_report --days-back 30
.venv/bin/python -m weather_bot.jobs.shadow_ensemble_report --days-back 30
.venv/bin/python -m weather_bot.jobs.ensemble_calibration_report --days-back 30
.venv/bin/python -m weather_bot.jobs.forecast_update_lag_report --days-back 30 --limit 2500
```

Expected smoke: `72 passed`, backtest ≈ `+$14` lead-aware improvement on
Apr 1–May 6.

## 5. Monitoring cadence

```bash
# Daily
.venv/bin/python research/monitor_edge_accuracy.py --hours 24

# Weekly / after enough new settled fills
.venv/bin/python research/profile_calibration.py --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD>
.venv/bin/python research/backtest_variance_fix.py --start <YYYY-MM-DD> --end <YYYY-MM-DD>
```

Use ≥30-50 settled fills before changing multipliers again.

## 6. Tuning rules

L1 overconfident `> +0.15` on a fresh sample → try `1.30x` (not blanket
all-lead). L1 underconfident `< −0.05` → try `1.20x`. Single-station drift →
prefer station-specific calibration over global multiplier changes.

## 7. True ensemble status (shadow only)

Sources: `GFS_ENS`, `ECMWF_IFS_ENS`, `ECMWF_AIFS_ENS`, `WEATHERNEXT2`
(via Google BigQuery once configured).

**2026-05-16 verdict:** do not promote WeatherNext wholesale. 1,200 settled
signal rows: original Brier `0.0947`, true-ensemble shadow Brier `0.1925`
(+0.0978, worse). Only KMDW lead-1 improved (`0.1558 → 0.1445`).

Ensemble-calibrated members (EMOS-lite) still lose to current bot
probabilities: holdout Brier `0.0386` (original) vs `0.1503` (calibrated members).

The empirical probability calibrator is the only calibration change that
currently improves Brier (`0.1978 → 0.1924` walk-forward, 1,007 signals
2026-04-16 to 2026-05-16).

**Next step:** keep WeatherNext shadow-only. Build a station/lead-gated
challenger report; only candidate slice today is KMDW lead-1.

## 8. PolymarketWeather-inspired research findings (2026-05-17)

- `jobs.ensemble_calibration_report`: EMOS-lite bias/spread calibration.
- `jobs.forecast_update_lag_report`: edge-z buckets vs 15/30/60m signed
  market movement. Small positive signal in 15-60m after a fresh forecast
  update (`+0.0111` signed 30m); OPEN-only moved against us (`−0.0114`
  signed 60m). Points to better timing/reprice, not larger sizing.
- `jobs.ai_context_brief`: deterministic context pack at
  `skills/weather-prediction-context/SKILL.md`.

## 9. Standing research priorities

Each must tie to a settled-fill metric with a go/no-go threshold:

1. **True ensemble value** — strict as-of replay; keep shadow-only until a
   station/lead slice improves Brier on ≥50 settled signals.
2. **WeatherNext 2 access and value** — same bar.
3. **Kalshi price formation** — central-limit-order-book, not bookmaker
   odds. Research what active makers anchor to.
4. **Settlement mechanics** — CLI timing, local standard time, 6h/24h
   consistency checks, revision/delay. Edge independent of forecast skill.
5. **Market microstructure** — spread, depth, snapshot age (current avg
   864s), orderbook imbalance, two-bracket late markets.
6. **Cross-platform gaps** — Polymarket KLGA/KORD vs Kalshi KNYC/KMDW
   plus station-adjusted forecasts.

## 10. Backlog (next highest-leverage work)

- **Point-in-time replay harness** (in progress) — evaluate every historical
  signal using only data available at signal `ts`. Produces Brier/CRPS/PnL
  stratified by station/lead/cycle. Unblocks every "should we tune X" question.
- **Cross-bucket smile-arbitrage scanner** (in progress) — sum bucket
  probabilities per event, flag inconsistencies, rank cheapest mispriced
  bucket. Edge independent of forecast skill.
- **EMOS-stratified ensemble blend** — calibrate per (station, lead, season),
  then sweep blend weights via stacked-CRPS against current production.
- **Isotonic recalibration** — on top of the decile calibrator, monotonic
  constraints, nightly retrain.
- **Settlement-window uncertainty** (after 2 PM same-day TMAX): probability
  final climb exceeds observed-so-far given hour, cloud, dewpoint, HRRR delta.
- **Cross-station neighbor residuals** — already ingested via
  `NEIGHBOR_STATIONS`, not yet a feature in the fair price.
- **NWS AFD text features** — grep `uncertainty`, `MOS too cool`, `inversion`,
  `marine layer`. Small categorical adjustment to variance.
- **Active two-sided maker simulation** — extend `paper_order` to both legs.
- **Auto-promotion rules** for paused/down-sized stations and bands.

## 11. Important non-changes

- `PAPER_MODE` stays default.
- Agreement gate remains diagnostic only (backtest cost `$240` over 30d).
- Divergence guardrail stays at `0.50` until larger sample post-corrected
  calibration.

## 12. Files that encode the methodology

- `models/distribution.py` — lead-day helper, variance multipliers, widening caps
- `strategy/ev.py` — order-level fee formula, divergence guardrail
- `strategy/profitability.py` — station/lead/price-band sizing controls, paper bypass
- `strategy/probability_calibration.py` — empirical calibrator, fallback ladder
- `main.py` — orchestrator, station-local bias gate, tripwire bypass
- `config.py` — all tunable parameters, gate defaults
- `research/profile_calibration.py` — side-adjusted profiler
- `research/backtest_variance_fix.py` — historical as-of replay
- `research/monitor_edge_accuracy.py` — live monitor
- `dashboard/queries.py`, `jobs/health_check.py` — side-adjusted expected P&L
- `jobs/profitability_report.py` — maker/wait, early-exit, divergence research
- `jobs/forecast_benchmark_report.py` — NBM/HRRR/GFS/ECMWF vs CLI truth
- `jobs/shadow_ensemble_report.py` — shadow-only ensemble replay
