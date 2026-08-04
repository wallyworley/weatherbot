# Walk-Forward Probability Calibration Backtest — 2026-05-22

_generated 2026-05-22 22:17 UTC_

Window: `2026-05-01` through `2026-05-22`.

This is a signal/opportunity replay. It uses only calibration evidence known before each signal timestamp. P&L is an unconstrained entry replay, so use Brier/calibration first.

## Overall

| signals | calibrated | raw YES Brier | calibrated YES Brier | delta | raw side Brier | calibrated side Brier | delta | raw opens | calibrated opens | raw P&L | calibrated P&L |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1265 | 1100 | 0.1892 | 0.1821 | -0.0071 | 0.1892 | 0.1821 | -0.0071 | 969 | 999 | $-7046.93 | $-7836.64 |

## By Station

| station | n | calibrated | raw YES Brier | cal YES Brier | delta | raw opens | cal opens | raw P&L | cal P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| KMDW | 415 | 374 | 0.1802 | 0.1750 | -0.0051 | 316 | 321 | $-1627.59 | $-2342.69 |
| KMIA | 403 | 359 | 0.1980 | 0.1866 | -0.0115 | 333 | 346 | $-2677.64 | $-2492.72 |
| KNYC | 447 | 367 | 0.1897 | 0.1847 | -0.0050 | 320 | 332 | $-2741.70 | $-3001.23 |

## By Lead Day

| lead | n | calibrated | raw YES Brier | cal YES Brier | delta | raw opens | cal opens | raw P&L | cal P&L |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 487 | 421 | 0.2046 | 0.1969 | -0.0077 | 392 | 410 | $-2589.84 | $-2868.33 |
| 1 | 778 | 679 | 0.1796 | 0.1729 | -0.0067 | 577 | 589 | $-4457.09 | $-4968.31 |

## Interpretation

- Negative Brier delta is good: calibration improved probability accuracy.
- Lower calibrated opens is expected if the model was overconfident.
- P&L here ignores portfolio/open-position constraints. Treat it as an entry-quality stress test, not a trading ledger.