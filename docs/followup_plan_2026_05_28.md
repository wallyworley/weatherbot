# Follow-up plan — items deferred from the 5/28 settlement hotfix

These items were intentionally kept out of [`fix_plan_2026_05_28.md`](fix_plan_2026_05_28.md) because they have no settlement-deadline pressure and would have added unnecessary surface area to a focused fix. Pick them up after the settlement work is closed.

## A — Dashboard `queries.py` timezone

**What's wrong**: `dashboard/queries.py` has 18 `CURRENT_DATE` references that feed `app_v2.py` panels. Between 8 PM ET and midnight ET, those queries roll over a day early because the VPS runs UTC. The user sees the Today page report "no fills today" or show tomorrow's date.

**Scope**: 18 references, multiple semantic categories. They are NOT just `valid_date = CURRENT_DATE` filters; some are `ts >= CURRENT_DATE`, some compute `days_to_settle`, some bound bias-cell month lookups.

**Proposed approach**:
1. Read every `CURRENT_DATE` line and label it: "user-facing today" vs "UTC operational timestamp."
2. For user-facing ones, replace with `(now() AT TIME ZONE 'America/New_York')::date`.
3. For UTC ones, leave alone.
4. Produce a diff for review before applying.

**Cross-cutting risk**: `dashboard/queries.py` is imported by the v1 dashboard (`app.py`) too. Any change affects both. If v1 dashboard semantics are intentionally UTC, splitting needs care.

**Verification**: open `https://v2.40-160-233-235.sslip.io` at 9 PM ET, confirm Today page shows current ET date and a non-empty fill list when fills exist. Open `https://40-160-233-235.sslip.io` (v1), confirm nothing visually regressed.

**Owner**: this agent (you). Not blocked on anything; can ship same day as settlement fix once that's confirmed clean.

## B — Timer reorder so settled-pull precedes settle

**Current**:
- `weatherbot-cli.timer` 13:23 UTC
- `weatherbot-settle.timer` 14:23 UTC
- `weatherbot-kalshi-settled.timer` 15:00 UTC (after settle)

**Proposed**:
- `weatherbot-cli.timer` 13:23 UTC
- `weatherbot-kalshi-settled.timer` 14:00 UTC
- `weatherbot-settle.timer` 14:23 UTC

**Only useful if Item C lands** (otherwise the Kalshi value is display-only and the timing doesn't matter).

**Files to change**:
- `/etc/systemd/system/weatherbot-kalshi-settled.timer` (VPS): `OnCalendar=*-*-* 14:00:00 UTC`
- `systemd/weatherbot-kalshi-settled.timer` (repo): same

**Verification**: `systemctl list-timers --no-pager | grep weatherbot` should show kalshi-settled before settle.

## C — Switch `settle_paper_fills.py` to prefer Kalshi `expiration_value`

**Why**: Kalshi's `expiration_value` is the actual settlement number — the bottom-line authority. Our `cli_obs.tmax_f` is a proxy that occasionally drifts (e.g., when actual peak occurs after the last NWS issuance our parser can use). Aligning settle to Kalshi removes one whole class of bug.

**Proposed change** in `jobs/settle_paper_fills.py`:
```python
def _get_obs_value(station, valid_date, var, ticker):
    # 1. Kalshi authoritative settlement value
    kalshi = _get_kalshi_expiration_value(ticker)
    if kalshi is not None:
        return kalshi, "KALSHI"
    # 2. NWS CLI fallback
    if var == "TMAX_DAILY":
        cli = nws.get_cli_tmax(station, valid_date)
        if cli is not None:
            return cli, "CLI"
    elif var == "TMIN_DAILY":
        cli = nws.get_cli_tmin(station, valid_date)
        if cli is not None:
            return cli, "CLI"
    # 3. Defer
    return None, "pending"
```

New helper:
```python
def _get_kalshi_expiration_value(ticker):
    sql = """
        SELECT NULLIF(payload->>'expiration_value','')::float
          FROM kalshi_market WHERE ticker = %s
    """
    with persistence.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, (ticker,))
        row = cur.fetchone()
    if not row: return None
    val = row[next(iter(row.keys()))] if isinstance(row, dict) else row[0]
    return float(val) if val is not None else None
```

**Depends on**: Item B (timer reorder). Without B, Kalshi value won't be present at settle time and we'll always fall back to CLI — same behavior as today.

**Risk**:
- If Kalshi's settlement is later corrected (extremely rare), our paper P&L follows the corrected value. Probably desirable for a paper simulator.
- Couples paper settlement to the daily settled-pull. If the settled-pull breaks (e.g., Kalshi 502s like we saw earlier today), we silently fall back to CLI — which is acceptable.

**Verification**:
- After deploy, run settle on a day where Kalshi and CLI disagreed by 1°F on a non-boundary bucket (none of our existing 10 known-divergence days flipped outcomes, but use one for the log-source check).
- Settled fills should log `obs=X (KALSHI)` not `(CLI)`.
- Run the Step 7 audit query — zero rows.

## D — Validate (or drop) the `for_date == target` YESTERDAY edge

**Reviewer claim**: "Live NWS products show valid morning-after YESTERDAY sections with `for_date == target`."

**What's unclear**: I have not seen a real NWS product matching this pattern in our current data. The reviewer didn't cite a specific product_id.

**Plan**:
1. For each of the 21 active stations, pull all CLI products from the NWS API over the last 7 days (or use IEM archive for older).
2. For each product where the body contains a YESTERDAY section, extract:
   - `for_date` from the title
   - The maximum value in YESTERDAY
   - The product's `issuanceTime`
3. Check whether any product has `issuanceTime.date() > for_date + 1`. If yes, that's the pattern the reviewer described.
4. If found, modify `_select_cli_for_target` to accept YESTERDAY whenever it parses, regardless of `for_date`.
5. If not found, no code change; add a note to this doc with the evidence reviewed.

**Risk if skipped**: low. The current code rejects only an edge case we have no evidence of in live data.

## E — Test fixtures for every active WFO

**Why**: the unit test introduced in the hotfix covers KAUS (KEWX), KNYC (KOKX), and KDFW (KFWD). The other 18 stations still rely on the bb6d042 parser working correctly. If any WFO has unusual formatting (e.g., extra columns in the temperature table, or section headers without leading whitespace), we'll catch it the same way we caught the KEWX bug — by accident, after it costs money.

**Plan**: capture one fixture per WFO that covers our 21 active stations. Add to `tests/test_nws_cli_parser.py`. Verify all pass.

**Risk if skipped**: an unknown WFO has unknown breakage. Low probability per station but cumulative across 21.

---

## Suggested ordering of follow-up work

1. **A (queries.py timezone)** — most user-visible after settlement is fixed.
2. **D (YESTERDAY edge validation)** — cheap, removes uncertainty from the reviewer feedback.
3. **E (fixture coverage)** — moderate effort, high long-term payoff.
4. **B+C together (timer reorder + Kalshi as source)** — biggest design change; do last, after the hotfix has settled in production for a few days.

Items B and C should be paired. Doing B alone has no functional effect; doing C alone introduces a race against the existing timer schedule.
