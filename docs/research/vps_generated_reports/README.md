# VPS generated reports (archived 2026-08-04)

Machine-generated research reports recovered from the retired VPS at
`/opt/weather_bot/research/reports/` before the host was decommissioned.

**Why these are here.** That directory was covered by a `research/reports/.gitignore`
containing `*`, so none of these files were ever eligible to be committed or pushed. They
existed only on the VPS. They are the primary evidence behind several closed experiments, so
they were moved into version control rather than deleted with the host.

**What this covers.** 98 files, including the generated results for EXP-2026-011 (latency),
EXP-2026-013 (shadow ensembles), EXP-2026-014 (favorite-longshot), EXP-2026-015 (venue-wide
sweep), EXP-C2 (nowcast), the market-relative center benchmark, and the operational history
(profitability reports, replay harness, execution quality, smile-arbitrage, shadow ensemble and
stored-forecast benchmarks).

**What is deliberately not here.** `market_information_forensics_2026-06-08.csv` (134 MB of raw
per-snapshot data for the closed EXP-2026-009 backbone) was too large to belong in git. It was
archived to local storage outside this repo, along with the final `.env` files (secrets) and the
2026-05-09 full database dump, which was also uploaded to the existing Google Drive folder
`weatherbot-vps-backup-20260702` because it holds `market_snapshot`, `metar_obs`, `det_forecast`
and `prob_forecast`, tables the 2026-07-02 results dump does not contain. See the retirement
note in `STATE.md`.

**Status.** Historical record only. The program is retired, all axes are closed, and nothing
here is regenerated. The canonical EXP-2026-011 scoring lives in `../EXP_2026_011_RESULTS.md`;
the specific run it scores is preserved separately as
`../EXP_2026_011_EVIDENCE_RUN_2026-06-23.md`.
