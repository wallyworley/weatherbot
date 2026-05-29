# Session handoff for a fresh reviewer — 2026-05-29

**You are**: a fresh Claude session (any model — Opus 4.7, 4.8 if it exists, Sonnet, doesn't matter). The prior session was Opus 4.7. The user wants a second pair of eyes.

**Your job**: review the prior session's work, validate it independently, and make any corrections you find necessary. The user explicitly does NOT want you to just confirm — they want you to push back where evidence is weak.

---

## How to bootstrap

```bash
cd /Users/walterworley/dev/weather_bot
git log --oneline -15                # last 15 commits, all this session and yesterday's
.venv/bin/python -m pytest tests/ -q  # should report 98 passed
```

The two prior review docs that build context:
- `docs/audit_2026_05_28.md` — yesterday's audit by the previous Opus 4.7 session
- `docs/independent_review_2026_05_29.md` — my (this session's) review of yesterday's work plus today's new changes

Don't trust either. Verify everything against the code and DB state yourself.

## Where the state lives

| Thing | Location |
|---|---|
| Code | `/Users/walterworley/dev/weather_bot` (Mac) and `/opt/weather_bot` (VPS, rsync deploy) |
| Postgres | local tunnel: `postgresql://weather:weather@localhost:5433/weather_bot`. VPS direct: 40.160.233.235:5432 |
| VPS SSH | `ssh -i ~/.ssh/id_ed25519 ubuntu@40.160.233.235` |
| Logs (VPS) | `/var/log/weather_bot/<job>.log`, also `journalctl -u weatherbot-*.service` |
| Memory | `/Users/walterworley/.claude/projects/-Users-walterworley-dev-weather-bot/memory/` |
| Dashboards | https://40-160-233-235.sslip.io (legacy v1), https://v2.40-160-233-235.sslip.io (v2). user `wally` |

Bot is in **paper mode** — no real money at risk regardless of what you find.

## Recent commits worth looking at, in order

```
36c88e2 fix(tests): update widen-factor assertion after 1.10→1.40 change
        + docs/independent_review_2026_05_29.md
4036acb feat(trading): block YES-tail/NO-consensus entries; widen fair_prob distribution
b71a190 feat(settle): prefer Kalshi expiration_value over our CLI capture
df8558e fix(dashboard-queries): use ET-relative dates instead of UTC CURRENT_DATE
7254a9b fix(cli): anchor section detection on data lines, accept YESTERDAY regardless of for_date
42d5f18 fix(dashboard-v2): NULLIF empty expiration_value before float cast
```

Use `git show <hash>` to read each diff. The big-picture story:

- **Yesterday** (5/28): a previous Opus 4.7 session fixed a CLI parser bug (KEWX/KBOU stations were silently grabbing overnight lows as daily maxes ~25% of days), corrected 28 mis-settled fills (-$922 phantom P&L), changed settle to prefer Kalshi's `expiration_value` over our CLI, converted dashboard timezone references from UTC to ET.
- **Today** (5/29): this session diagnosed bot fair_prob is systematically 10-18 pp over-confident across nearly every probability bucket, shipped two new entry gates (YES_TAIL_GATE blocks YES <$0.20, NO_CONSENSUS_GATE blocks NO ≥$0.60), and bumped the same-day distribution widening factor from 1.10 → 1.40.

## The current state of the bot (as of 2026-05-29 ~16:00 UTC)

- 19 active trading stations (graduated from 3 on 5/26)
- Trading lead=0 only (`MAX_LEAD_DAY_TO_TRADE=0`)
- Take-profit threshold 0.70 (`TAKE_PROFIT_THRESHOLD=0.70` in VPS .env)
- 7-day net P&L: **negative** (-$118 yesterday, +$105 day before, mostly red)
- Today's realized P&L mid-day: see `pnl_today()` in `dashboard/queries.py`

## What the prior session (me) claims today, in one line each

1. The bot's fair_prob is systemically over-confident by 10-18 pp across every probability bucket above 20%. **Verify** with the SQL in `docs/independent_review_2026_05_29.md` section "Probability calibration module".
2. YES bets at price <$0.20 lost 0/95 held-to-settlement over 14 days. **Verify** by running the cohort query from chat history of 2026-05-29.
3. NO bets at price ≥$0.60 lost net -$150 over 14 days (held -$608, exit +$458). **Verify** same way.
4. Blocking both categories would have improved 14-day P&L by +$339. **CAVEAT**: this is 100% in-sample.
5. The existing probability calibrator (`strategy/probability_calibration.py`) IS already shrinking probabilities, but with `PROB_CALIBRATION_PRIOR_N=35` it only applies ~42% of the empirical correction for current sample sizes (n≈25 per bucket).
6. The widen-factor bump (1.10 → 1.40) interacts with the calibrator in a way I didn't analyze. **Open question for you.**

## Issues from my review (open, ranked)

| Severity | Issue | Where to look |
|---|---|---|
| MED | Widen-factor × calibrator interaction not analyzed | `models/distribution.py` + `strategy/probability_calibration.py` |
| MED | NO trades now squeezed into [$0.50, $0.60) — could over-starve | `main.py:299-321` |
| MED | Block-gates trust market over bot's edge calc; coarse heuristic | `main.py` two new gates |
| MED | 14-day backtest is purely in-sample (overfit risk) | review methodology |
| MED | `PROB_CALIBRATION_PRIOR_N=35` too sticky for current sample sizes | `strategy/probability_calibration.py:65` |
| LOW | `MIN_EDGE_BPS` is price-relative; lets cheap-tail noise through | `strategy/ev.py:150,162` + `config.py:264` |
| LOW | No stop-loss; structural asymmetry causes daily bleed | `strategy/early_exits.py` |
| LOW | METAR-vs-CLI memo (`measured_findings_2026-05-01.md`) overstates undercount | memory file |
| LOW | Settled-pull at 14:00 UTC, settle at 14:23 UTC — 22-min race window | systemd timer schedule |

## Specific things I'd love a second opinion on

1. **Did I draw the right conclusion from the calibration table?** The 10-18 pp overconfidence is real, but I jumped to "widen the distribution" without ruling out simpler explanations (bias correction error, sample-selection bias from take-profit captures, lead-day misclassification). Look hard at the calibration audit's methodology in [docs/independent_review_2026_05_29.md](docs/independent_review_2026_05_29.md) section 2 — specifically the bullet "my audit grouped by `model_p_side` … without separating early-exit fills from held fills."

2. **Did I ship the right fix?** I shipped two entry gates plus a widen-factor bump. Three changes at once is generally bad form. The cleaner intervention is probably tuning `PROB_CALIBRATION_PRIOR_N` alone. **Should the entry gates be reverted?**

3. **Is the widen-factor change actually doing what I claim?** Run a quick simulation: build a distribution for KMSY 5/28 with widen=1.10 and widen=1.40. Compare interior knot widths. Then look up the calibrator's decile bins for the resulting fair_probs and see whether the calibrator's correction direction is consistent under both.

4. **Is the calibration audit query correct?** SQL is in `docs/independent_review_2026_05_29.md`. Specifically: I bucketed by `model_p_side` which for NO bets is `1 - fair_prob`. Is that the right thing to bucket on, or should it be raw fair_prob regardless of side? Different answer changes the conclusion.

5. **Look at the new gates' interaction with `PROFIT_GATE`.** `PROFIT_SIZE` already reduces size on unprofitable cells. My gates block entries. Together they may be doing too much: blocking what PROFIT_GATE was already sizing down, and sizing down what my gates would have blocked. Inspect `signal.notes` for trades that have BOTH `PROFIT_SIZE` and `YES_TAIL_GATE` or `NO_CONSENSUS_GATE` — though SKIPs don't have PROFIT_SIZE applied so this may be a non-issue.

## Verification queries (copy-paste ready)

### A) Confirm the three-way audit (yesterday's work) is still clean

```sql
-- A.1: cli_obs ↔ Kalshi expiration_value
SELECT count(*) AS bad FROM (
  SELECT km.station, km.valid_date
  FROM kalshi_market km
  LEFT JOIN cli_obs co ON co.station=km.station AND co.local_date=km.valid_date
  WHERE NULLIF(km.payload->>'expiration_value','') IS NOT NULL
    AND km.valid_date >= '2026-05-20' AND co.tmax_f IS NOT NULL
  GROUP BY km.station, km.valid_date, co.tmax_f, km.payload->>'expiration_value'
  HAVING co.tmax_f != NULLIF(km.payload->>'expiration_value','')::float
) x;
-- Want: 0
```

```sql
-- A.2: settled paper_fill payouts ↔ Kalshi expiration_value
SELECT count(*) AS bad FROM (
  SELECT pf.id FROM paper_fill pf
  JOIN kalshi_market km ON km.ticker = pf.ticker
  WHERE pf.payout IS NOT NULL AND pf.exit_price IS NULL
    AND NULLIF(km.payload->>'expiration_value','') IS NOT NULL
    AND pf.payout != CASE WHEN (pf.side='YES' AND NULLIF(km.payload->>'expiration_value','')::float >= COALESCE(km.lower_f, -999)
                                              AND NULLIF(km.payload->>'expiration_value','')::float < COALESCE(km.upper_f, 999))
                              OR (pf.side='NO' AND NOT (NULLIF(km.payload->>'expiration_value','')::float >= COALESCE(km.lower_f, -999)
                                                        AND NULLIF(km.payload->>'expiration_value','')::float < COALESCE(km.upper_f, 999)))
                            THEN 1.0 ELSE 0.0 END
) x;
-- Want: 0
```

### B) Confirm today's gates are actually firing

```sql
SELECT skip_reason, count(*) FROM signal
WHERE ts > now() - interval '1 hour' AND action = 'SKIP'
GROUP BY skip_reason ORDER BY count DESC;
-- Want: YES_TAIL_GATE and NO_CONSENSUS_GATE both present with non-zero counts
```

### C) The calibration audit query (reproduce my conclusion)

```sql
WITH fills AS (
  SELECT pf.id, pf.side,
         CASE WHEN pf.side='YES' THEN s.fair_prob ELSE 1-s.fair_prob END as model_p_side,
         CASE WHEN (pf.payout = 1) OR (pf.exit_price > pf.price) THEN 1 ELSE 0 END as side_won
  FROM paper_fill pf
  LEFT JOIN signal s ON s.id=pf.signal_id
  JOIN kalshi_market km ON km.ticker=pf.ticker
  WHERE km.valid_date >= '2026-05-15' AND s.fair_prob IS NOT NULL
    AND (pf.payout IS NOT NULL OR pf.exit_price IS NOT NULL)
)
SELECT
  CASE WHEN model_p_side < 0.10 THEN '00-10'
       WHEN model_p_side < 0.20 THEN '10-20'
       WHEN model_p_side < 0.30 THEN '20-30'
       WHEN model_p_side < 0.40 THEN '30-40'
       WHEN model_p_side < 0.50 THEN '40-50'
       WHEN model_p_side < 0.60 THEN '50-60'
       WHEN model_p_side < 0.70 THEN '60-70'
       WHEN model_p_side < 0.80 THEN '70-80'
       WHEN model_p_side < 0.90 THEN '80-90'
       ELSE '90-100' END as bucket,
  count(*) n,
  round((avg(model_p_side)*100)::numeric, 1) bot_pred,
  round((avg(side_won::float)*100)::numeric, 1) actual,
  round(((avg(model_p_side) - avg(side_won::float))*100)::numeric, 1) overconf
FROM fills GROUP BY 1 ORDER BY 1;
```

**Expected if my conclusion is correct**: overconf column shows +10 to +18 pp from the 20-30 bucket upward.

**Expected if my conclusion is overstated**: split this by `(pf.exit_price IS NULL)` to isolate held-only fills, and compare. If held-only overconfidence is much higher than mixed, the mixed audit was understating it. If they're similar, my conclusion holds.

### D) Check if entry gates are eating too much volume

```sql
-- Volume of OPENs by date, before and after today's gate deploy (~15:00 UTC)
SELECT ts::date AS day,
       count(*) FILTER (WHERE action='OPEN') as opens,
       count(*) FILTER (WHERE skip_reason='YES_TAIL_GATE') as ytg,
       count(*) FILTER (WHERE skip_reason='NO_CONSENSUS_GATE') as ncg
FROM signal WHERE ts > now() - interval '3 days'
GROUP BY 1 ORDER BY 1;
```

Today's OPEN count should not be dramatically lower than the 5/28 / 5/27 OPEN counts. If it is, the gates are too aggressive.

### E) Check the calibrator is actually firing

```sql
SELECT count(*) FILTER (WHERE notes LIKE 'CAL%') as has_cal_note,
       count(*) FILTER (WHERE notes NOT LIKE 'CAL%') as no_cal_note
FROM signal WHERE ts > now() - interval '15 min' AND action = 'OPEN';
```

Should be ~100% has_cal_note. If many missing, the calibrator silently isn't firing somewhere.

## Production deploy/restart commands (in case you need them)

```bash
# Run tests locally
.venv/bin/python -m pytest tests/ -q

# Push to deploy (rsync to VPS auto-runs on push to main)
git push

# Wait for deploy and verify
ssh -i ~/.ssh/id_ed25519 ubuntu@40.160.233.235 'grep -q YES_TAIL_GATE /opt/weather_bot/main.py && echo deployed'

# Run a single main tick manually
ssh -i ~/.ssh/id_ed25519 ubuntu@40.160.233.235 'sudo systemctl start weatherbot-main.service'

# Settle (dry-run safe)
ssh -i ~/.ssh/id_ed25519 ubuntu@40.160.233.235 'cd /opt/weather_bot && .venv/bin/python -m weather_bot.jobs.settle_paper_fills --dry-run'
```

## How to revert if you find the changes are wrong

### Revert just the price gates (no redeploy needed)

```bash
ssh -i ~/.ssh/id_ed25519 ubuntu@40.160.233.235 'sudo tee -a /opt/weather_bot/.env <<EOF
MIN_YES_PRICE=0.00
MAX_NO_PRICE=1.00
EOF'
# Next main tick will pick up env values
```

### Revert the widen-factor change

```bash
# Edit models/distribution.py:79 — change `return 1.40` back to `return 1.10`
# Update tests/test_distribution.py:91 accordingly
# Commit and push (auto-deploys)
```

### Revert all of today's changes

```bash
git revert 36c88e2 4036acb  # in that order
git push
```

## What I want you to NOT do

- Don't add a stop-loss yet. Discussed as a future change; user wants to observe gates first.
- Don't touch the systemd timers (`weatherbot-*.timer`). Confirmed working.
- Don't re-run `pull_cli --days-back N` to refresh historical CLI rows — yesterday's parser fix already did this; further refreshes would just re-overwrite with the same values.

## A note on memory

Read `MEMORY.md` first; it has 1-line summaries of every prior decision. The two memory files most relevant to today's work:
- `calibration_overconfidence_fix_2026_05_29.md` — what I shipped today
- `settle_metar_fallback_bug_2026_05_28.md` — yesterday's settle fix

If your review finds I was wrong, update these memory files with the correction. The user reads them between sessions.

---

End of brief. Be skeptical. Push back where the evidence is thin. The user explicitly invited adversarial review.
