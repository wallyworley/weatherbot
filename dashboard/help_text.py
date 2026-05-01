"""Plain-English help and legend text for the dashboard.

Rule of thumb when editing: if a friend who's never read a stats textbook
can't figure out what a metric means in 10 seconds, the text is wrong.
Lead with what the number means; put the technical name in parentheses.
"""

OVERVIEW = """
**What this is.** A live look at how the trading bot is doing — is it healthy,
is it making smart bets, is it losing money, is anything broken? Refreshes
every 15 seconds.

**The 5-second test.** Look at the row of colored tiles at the top of the
**Status** tab. If they're all green, the bot is fine and you can close the
tab. If any are yellow or red, click into the matching tab to see what's up.

**Color rules.**
- 🟢 GREEN — everything's normal.
- 🟡 YELLOW — something's drifting; not urgent but worth watching.
- 🔴 RED — out of normal range. **For some categories (the bot's accuracy,
  the size of open trades, recent profits/losses), the bot will refuse to
  place new trades on this station until you click "Ack" to acknowledge
  you've seen it.** A data feed going RED is informational only — it means
  a fetcher is broken; trades naturally stop because there's no fresh data
  to score with.

**Acknowledging an alert.** On the Status tab, click "Ack" next to any red
banner. This tells the bot "I've seen it, you can keep trading on this
station." Only do this after you've checked the dashboard tab and decided
the issue is benign or already fixed.

**Text alerts.** When something flips RED, you'll get a macOS notification
(and optionally an iMessage if `ALERT_PHONE` is set in `.env`). You won't
get spammed — each red event alerts once, and won't fire again until it
recovers and goes red again later.
"""

STATUS_TAB = """
Each tile is a quick health summary, refreshed every 30 minutes by a
background job (`jobs/health_check.py`).

**DATA tile** — are the data feeds (weather forecasts, observations, Kalshi
markets) actually being pulled in? Red = a fetcher is broken or hasn't run
in a while. **First place to check when other tiles look weird.** If data
is RED and the model tile is YELLOW, it's almost always the data, not the
model.

**MODEL tile** — is the bot's confidence accurate? Built from two pieces:
1. **Accuracy score** — how often the bot's "70% confidence" trades actually
   win 70% of the time. Lower number is better. Around 0.14 is healthy;
   above 0.20 means the bot is consistently over- or underconfident.
2. **Profit gap** — how much the bot expected to make vs what it actually
   made, per trade. If the bot expected +$10/trade and only made +$5, the
   gap is −$5/trade. Big negative gaps mean the bot is overrating itself.

**Real example:** On 2026-04-30 the profit gap hit −$196 across the day's
trades — the bot thought it was earning, reality said no. Had this dashboard
existed, the MODEL tile would have flipped RED on 04-29 evening and the bot
would have stopped opening new trades the next day instead of doubling down.

**MARKETS tile** — how many Kalshi weather markets are open right now to
trade. Few markets = bot has nothing to do. Usually a Kalshi outage or
weekend.

**RISK tile** — how much money the bot has tied up in open trades, as a
fraction of your bankroll. Red means the bot has stacked a lot of bets;
worth a look in case something is mis-sized.

**P&L tile** — how much the bot has made or lost in the last 7 days of
completed trades. Negative is okay in paper mode; very negative breaks the
threshold and prompts investigation.
"""

CALIBRATION_TAB = """
**This tab exists so you'll catch the next bad streak before it costs $200.**

**Daily expected vs actual profit chart** — each dot is one day's average
"profit gap per trade" — how much the bot thought it would make minus what
it actually made. The yellow band is the warning zone; the red band means
something's wrong with the bot's confidence. **One stray dot above the band
is noise; a string of dots trending up means the model is drifting.**

**Accuracy by station chart (Brier score)** — lower is better. Think of it
as "how surprised the bot is by what actually happened." A perfect predictor
scores 0; a coin-flip scores 0.25; the bot's healthy zone is around 0.14.
Above 0.20 = bot is consistently miscalibrated. New stations start with a
small number of trades — give them a week before reading much into their
score.

**Reliability chart** — when the bot says "I'm 70% confident," does it
actually win 7 times out of 10? This chart bins trades by the bot's
confidence and plots that against the actual win rate. If the dots sit on
the diagonal line, the bot is well-calibrated. Bowing below the line means
"it claims more confidence than it deserves." Bowing above means the
opposite (less common).

**Bias drift events** — overnight, the bot relearns each station's quirks
from new weather data. If today's learning is wildly different from
yesterday's (a big jump in a single night), it usually means a data feed
broke and fed garbage into the learning. The drift detector flags these
automatically. **This is the canary that would have caught the corrupted
NBM data on 04-30 before any trades happened.**
"""

TRADING_TAB = """
**Open positions** — every paper trade currently in flight. The "MTM"
column is the current value of the bet; positive means it's looking like
a winner, negative means trouble. Settles automatically the morning after
the weather day passes.

**Today's signals** — every market the bot looked at today, every 15
minutes. Filter by what the bot decided:
- `OPEN` — bot placed a paper trade.
- `SKIP/NO_EDGE` — the gap between bot and market wasn't big enough to
  cover Kalshi's fees.
- `SKIP/DIVERGENCE` — bot and market disagreed by more than 50 percentage
  points; bot refused to trade because that big a disagreement usually
  means the bot is broken, not the market.
- `SKIP/BIAS_GATE` — the bot doesn't have enough learned data for this
  station/month/timeframe yet. **Stations like KORD and KMIA will show this
  reason for the first 1–2 weeks until their data accumulates.**
- `SKIP/TRIPWIRE_RED` — Status tab has this station flagged red. Trades
  stop until you click Ack.
- `SKIP/FEE_LOAD` — Kalshi fees would eat more than 20% of the trade's
  cost. Mechanically unprofitable.

**Distribution preview** — the bot's current forecast for today's high
temperature at each station, drawn as a probability curve. The orange dashed
line is what HRRR (a faster, more recent weather model) is saying. The pale
green stripes are the Kalshi market buckets. **You can eyeball "do we agree
with the market?" — if the curve sits in a bucket the market is pricing
low, that's where the bot sees an opportunity.**
"""

DEEP_DIVE_TAB = """
**Counterfactual replay** — the question this answers is: "if we'd been
running with these settings instead, would yesterday's trades have gone
better or worse?" Pick a date range and flip the parameters (how wide the
forecast curve is, how much weight HRRR gets, etc.), then re-score every
historical trade. Compare new total profit against actual. **This is the
same tool we used to validate the recent forecast width change** — making
it permanent so future tuning is grounded in data instead of guesswork.

**NBM cycle inspector** — the weather forecast updates every 6 hours. For
any (station, weather day), this chart overlays every forecast we have for
that day. **Adjacent forecasts disagreeing by more than 3°F is the visible
fingerprint of a data ingestion bug** — the 04-30 corruption showed 18°F
swings between adjacent cycles, which would have been screamingly obvious
on this chart.

**Per-trade ledger** — every completed trade with: how confident the bot
was, what price it paid, won or lost, net dollars. Sortable. Use this when
the calibration tab says something's off and you want to find the specific
trades responsible.
"""

# Plain-English tooltips. Show up on metric labels throughout the UI.
METRIC_TOOLTIPS = {
    "brier":       "Accuracy score (Brier). Lower is better. 0 = always right, "
                   "0.25 = same as flipping a coin, above 0.20 means the bot's "
                   "confidence doesn't match reality.",
    "edge_gap":    "Total dollars the bot **expected** to win minus what it "
                   "**actually** won across all completed trades. Negative means "
                   "the bot was overestimating itself. The 04-30 incident hit −$196.",
    "edge_per":    "Same as above, but per trade — easier to compare across days "
                   "with different trade counts.",
    "open_n":      "Trades placed but not yet decided (the weather day hasn't "
                   "happened). Settles automatically the morning after.",
    "fair_prob":   "The bot's odds — its estimate of how likely the temperature "
                   "will land in this bucket. 0.30 means 'I think this happens "
                   "30 times out of 100'.",
    "divergence":  "How far apart the bot and the market are, scale 0 to 1. "
                   "0 = total agreement. Above 0.50 = bot refuses to trade "
                   "because that big a disagreement usually means the bot is wrong.",
    "kelly":       "How much of your bankroll the bot wants to bet on this trade, "
                   "after a safety multiplier. Hard-capped at 2% per trade no matter what.",
    "lag_min":     "Minutes since the most recent update from each data feed. "
                   "NBM (forecasts) updates every 6 hours, weather observations every "
                   "30 minutes, Kalshi every 15 minutes.",
    "sample_size": "How many real weather days the bot has used to learn this "
                   "station's quirks for this month and timeframe. Below 10 = "
                   "not enough data, the bot won't trade.",
    "shrink":      "How much the bot trusts its learned bias for this month. "
                   "Starts near 0 (low trust, new station) and grows toward 1 as "
                   "more weather days accumulate.",
    "delta_sigma": "How much the bot's learned bias jumped overnight, compared "
                   "to its normal day-to-day movement. Above 3 = something weird "
                   "happened — usually a data feed broke and fed bad data into "
                   "the learning step.",
}

# Standalone glossary — shown in the sidebar so you can decode any acronym
# without leaving the page.
GLOSSARY = """
**Accuracy score (Brier)** — how often the bot's confidence matches reality.
Lower is better. Healthy: ~0.14. Worry above 0.20.

**Bankroll** — the simulated trading capital. Default is $1000.

**Bias** — for each station, how much the weather forecast tends to be off
(too warm, too cold) for a given month and forecast horizon. The bot learns
this from history and corrects for it.

**Brier score** — see Accuracy score. The technical name is Brier; the plain
meaning is "how often is the bot right?".

**CDF / probability curve** — the forecast as a curve. X-axis is temperature,
Y-axis is "probability the high will be at most this temperature." Steep
curve = confident. Flat curve = uncertain.

**Calibration** — a fancy word for "is the bot's confidence trustworthy?".
A bot that says "70% chance" and is right 70% of the time is well-calibrated.

**Divergence** — how much the bot disagrees with the market on the odds of a
particular outcome. Big disagreements are either edge (bot is right) or
bugs (bot is broken).

**Edge** — expected profit per dollar bet, after fees.

**Fair price** — the bot's estimate of what a contract should cost. If the
bot's fair is 0.30 and the market is selling at 0.10, the bot sees edge.

**HRRR** — a fast, very recent weather model that updates every hour. The
bot uses it to refine the same-day forecast.

**Kelly fraction** — the math-optimal bet size given your edge. The bot
uses **quarter-Kelly** (a quarter of optimal) to be conservative, capped
at 2% of bankroll per trade.

**Kalshi** — the prediction market platform we trade on.

**Lead day** — how far in the future the forecast is. Today = 0, tomorrow = 1.

**METAR** — the standard-format weather observation reports from airport
weather stations. Source of "what actually happened."

**NBM / NBM QMD** — a probabilistic weather forecast that gives percentile
estimates ("there's a 90% chance the high is below 65°F") rather than a
single number. This is the bot's primary forecast source.

**Reliability** — see Calibration. Same idea, different name.

**Settled** — a paper trade where the weather day has passed and we know if
it won or lost.

**σ / sigma** — "how unusual is this move?" 1σ = normal day-to-day variation.
3σ = very unusual; the bot's learned bias jumping 3σ overnight almost always
means a data feed broke.

**TMAX / TMIN** — the day's high (TMAX) or low (TMIN) temperature in °F.
"""
