# Weather-bot performance evaluation — 2026-06-04

**Purpose:** independent review requested. The bot has been performing poorly;
this evaluates *why*, using live data (paper mode), and evaluates two external
weather traders we've been studying. Every claim below is tied to a query or
data pull; reproduction snippets are at the end so you can verify rather than
trust. **The bot is in paper mode — no real money is at risk.**

**One-line conclusion:** the bot has **no demonstrated edge right now** — held
bets lose ~100%, the "fix" we deployed (a station whitelist) selected the worst
stations out-of-sample, and our only mechanical lever (early exit) rescues ~23%
of losers. The two profitable external traders both win on **selection/forecast
skill held to resolution**, not on any exit trick we can copy.

---

## 1. Context (current state)

- Kalshi daily high/low temperature bracket markets, ~19 stations, paper mode.
- Trades same-day only (`MAX_LEAD_DAY_TO_TRADE=0`), quarter-Kelly sizing.
- Recent gates: divergence cap (`MAX_FAIR_MKT_DIVERGENCE=0.20`), probability
  calibrator, `TAKE_PROFIT_THRESHOLD=0.70`, entry-price gates (YES_TAIL,
  NO_CONSENSUS), and as of 2026-06-02 a **hard station whitelist**
  (`TRADE_STATION_WHITELIST=KAUS,KSAT,KBOS,KSFO,KNYC`) — only these five may open.
- The whitelist was chosen 2026-06-01 from an in-sample "positive hold-edge"
  audit.

## 2. The bot is genuinely losing — and it's not a settlement bug

Daily realized P&L (settled fills) is consistently negative and choppy. More
importantly, of **held-to-settlement positions opened since 2026-06-01: 0 wins
out of 43.** I verified this is real, not a mis-settlement:

| station | date | side | bucket (°F) | CLI high | result |
|---|---|---|---|---|---|
| KNYC | 06-03 | YES | [84, ∞) | 83 | miss by 1° |
| KSFO | 06-03 | YES | [66, 68) | 70 | miss by 2° |
| KBOS | 06-03 | YES | [82, 84) | 80 | miss by 2° |
| KSAT | 06-03 | YES | [86, 88) | 89 | miss by 1° |

Zero settlements lacked CLI truth (no mis-settle path). The bot is simply
picking the wrong bracket, usually by 1–3°F.

## 3. The whitelist (our current "fix") is enforced — and backwards

Enforcement confirmed by the open counts (non-whitelisted opens went to zero on
2026-06-02 and stayed there):

| date | whitelisted opens | non-whitelisted opens |
|---|---|---|
| 05-31 | 13 | 29 |
| 06-01 | 11 | 27 |
| 06-02 | 19 | **0** |
| 06-03 | 15 | **0** |
| 06-04 | 11 | **0** |

But out-of-sample (positions opened since 06-01), the whitelisted five are the
**worst** performers:

| | realized P&L since 06-01 |
|---|---|
| whitelisted 5 (KAUS/KSAT/KBOS/KSFO/KNYC) | **−$102** |
| everything else (legacy/pre-enforcement) | +$3 |

KAUS (+$16) is the only whitelisted station in the black. This is textbook
in-sample overfit: the cells that looked good historically lose out-of-sample.
**The station axis is a dead end** — and the scheduled 2026-06-09 re-eval, whose
job is to "re-check whitelisted hold-edge before un-pausing others," is asking a
question the data has already answered (negative).

## 4. Our only mechanical lever (early exit) is real but modest

Take-profit currently fires at 0.70 of max gain. For the 43 held losers since
06-01, did their sellable mark ever rally before decaying to zero?

| outcome | n | avg max gain |
|---|---|---|
| rallied to the 0.70 take-profit trigger | **0** | — |
| partial rally (≥0.10 absolute) | 16 | +0.25 |
| tiny rally (0.03–0.10) | 10 | +0.05 |
| never rallied (decayed) | 17 | −0.06 |

Translated into the take-profit *progress* metric (what the threshold actually
measures): **lowering the threshold 0.70 → 0.30 would have exited 10 of 43
losers; → 0.40 would catch 9.** So the exit lever is a real but partial
band-aid (~23%). The dominant problem is the ~17/43 that never rally — those are
**selection failures**, savable only by a stop-loss (cap the loss) or by not
entering them.

## 5. The two external traders — what they actually do (corrected)

### 5a. Polymarket trader (profile `0x594edB91…`, +$150K realized on weather)

**Correction / retraction:** an earlier note (and the auto-generated study)
claimed he "buys cheap and sells the intraday rally." **That is false.** His
public activity is **1,834 trades, every one a BUY, zero SELLs.** He exits via
**REDEEM** (winning shares pay $1) and **MERGE** (negative-risk bracket
structure), i.e. he **holds to resolution.**

What's actually true (from the data):
- Buy-only **barbell**: median entry **$0.048**, 54% of buys under 10¢, but also
  **43% above 50¢** (favorites). ~$60 average ticket.
- Massive **diversification** across global city-days (NYC, Tokyo, Beijing,
  Wellington, London, Atlanta, Chicago, …).
- Edge = **selection** (which brackets are underpriced vs true probability) +
  diversification, **held to settlement**. Not an execution/trading edge.
- Caveat: the exact per-position realized-P&L accounting on Polymarket's
  negative-risk markets (positive P&L on positions that resolved at price 0) is
  **not fully derivable** from the public data — we are deliberately not
  over-interpreting it.

### 5b. Prilo WeatherEdge (Reddit methodology source)

A forecaster: regime conditioning (Miami humidity runs hot, Chicago lake-breeze
suppresses, SFO marine cap), regime-dependent σ, and discipline ("most days have
no edge; sit out"). Tested against our own data, real station structure exists —
mean residual (CLI − raw NBM p50), last 45 days:

| station | mean resid (°F) | note |
|---|---|---|
| KMIA | +0.73 | NBM cold (actual hotter) — matches "Miami runs hot" |
| KNYC | −1.86 | NBM hot |
| KBOS | −1.79 | NBM hot |
| KORD | −1.51 | NBM hot |

(These are vs *raw* NBM; the bot applies a bias correction on top, so this shows
the structure exists — it does **not** by itself prove the bot is mis-correcting.
See open item in §7.)

## 6. Conclusion

- Both profitable traders win on **selection/forecast skill held to resolution** —
  knowing which brackets are underpriced. **Neither gives us a mechanical trick
  to copy.** They confirm the edge lives in *selection*, which is exactly where
  our bot fails (it bets where it disagrees with the market — winner's curse —
  and is wrong there; 0/43 held wins).
- Our deployed "fix" (whitelist) is the wrong axis and backwards out-of-sample.
- Our only mechanical lever (exit) is a ~23% band-aid.
- **Net: the bot does not currently have a demonstrated edge.** It's paper, so
  nothing is bleeding real money — the cost is wasted learning.

> **ACTIVATED 2026-06-05:** A1 (take-profit → 0.35), A2 (stop-loss
> `EARLY_EXIT_STOP_LOSS=-0.50`), and B3 (whitelist dropped) are now live on the
> VPS (paper). The user chose to keep trading rather than go observation-only
> (paper has no cost; goal is to watch the new exit logic work). These are
> damage-limiters + abandoning the failed axis — **not** a validated edge. C
> (selection) and D (prove offline) remain open. Revert: `.env.bak_20260605`.

## 7. Recommended concrete steps

**A. Do now — reversible, reduces some bleed (explicitly band-aids):**
1. `TAKE_PROFIT_THRESHOLD` 0.70 → **0.35** (env var, no redeploy). Saves ~10/43
   held losers; there are zero held winners to protect right now, so near-free.
2. Add a **stop-loss** (exit at progress ≤ −0.50) to cut the ~17/43 pure-decay
   losers. Symmetric to take-profit.

**B. Do now — stop running the dead axis:**
3. Drop the station whitelist as the lever (backwards out-of-sample). Don't wait
   until 06-09 to "re-check hold-edge" — it's negative basically everywhere.

**C. The real fix — selection (a build, not a toggle):**
4. **Cross-venue agreement as an entry filter.** On verified same-station cities
   (currently only KMIA, KATL — Polymarket settles different stations elsewhere:
   NYC=LaGuardia, Chicago=O'Hare, Denver=Buckley), only open when our model
   agrees with *both* Polymarket and Kalshi. Treat model-vs-both-markets
   disagreement as a reason to **skip**, not as edge.
5. **Verify the bias table neutralizes the §5b residuals** (compare CLI to the
   bot's *bias-corrected fair*, not raw NBM). If KNYC's −1.9°F NBM bias leaks
   into fair, that's a concrete fixable selection bug. **Not yet verified.**

**D. Reframe the experiment:**
6. Until something shows positive *held* edge out-of-sample in backtest, scaling
   paper trades just generates noise. Prove a selection edge offline first.

## 8. Open items / explicitly NOT verified

- §5b: whether the bias table actually removes the per-station NBM bias from the
  bot's fair (needs the bot's stored fair-median, which must be reconstructed).
- §5a: the exact negRisk realized-P&L mechanics on Polymarket.
- Sample size: ~43 held losers / ~45 whitelist opens over 06-02–06-04 is modest;
  the direction is corroborated by the 06-01 mark-to-settlement audit but is not
  a large sample.

## 9. Reproduction (for the reviewer)

DB: `postgresql://weather:weather@localhost:5433/weather_bot` (local tunnel) or
VPS `40.160.233.235:5432`. Key queries (paraphrased):

- **0 held wins:** `paper_fill` where `settled AND exit_price IS NULL AND
  payout=0 AND ts>='2026-06-01'`, joined to `cli_obs`; check `tmax_f` vs
  `[lower_f, upper_f)`.
- **Whitelist enforcement:** count opens per `ts::date` split by station ∈ the
  whitelist.
- **Per-station OOS P&L:** realized P&L by station for `ts>='2026-06-01'`.
- **Rally diagnostic:** for held losers, `MAX(yes_bid|no_bid)` from
  `market_snapshot` between fill `ts` and `+20h`, vs entry price and the
  `0.70*(1-entry)` take-profit trigger.
- **Residuals:** latest morning NBM p50 from `prob_forecast` vs `cli_obs.tmax_f`,
  grouped by station.
- **Polymarket trader:** `https://data-api.polymarket.com/{positions,activity,
  value}?user=0x594edB9112f526Fa6A80b8F858A6379C8A2c1C11` (public, no auth).
  Activity is all `side=BUY`; exits are `type` REDEEM/MERGE.

Tooling added this cycle: `jobs/polymarket_trader_watch.py` (study generator),
`research/polymarket_crosscheck.py` (verified-only venue comparison),
`jobs/reddit_watch.py` (Prilo monitor). See `docs/review_followups_2026_06_09.md`
for the synthesis framing.
