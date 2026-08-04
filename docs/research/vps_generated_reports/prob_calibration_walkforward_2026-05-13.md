# Walk-Forward Probability Calibration Backtest — 2026-05-13

_generated 2026-05-13 14:05 UTC_

Window: `2026-05-01` through `2026-05-13`.

This is a signal/opportunity replay. It uses only calibration evidence known before each signal timestamp. P&L is an unconstrained entry replay, so use Brier/calibration first.

## Overall

| signals | calibrated | raw YES Brier | calibrated YES Brier | delta | raw side Brier | calibrated side Brier | delta | raw opens | calibrated opens | raw P&L | calibrated P&L |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 647 | 485 | 0.1866 | 0.1828 | -0.0038 | 0.1866 | 0.1828 | -0.0038 | 518 | 521 | $-3456.01 | $-4039.73 |

## By Station

| station | n | calibrated | raw YES Brier | cal YES Brier | delta | raw opens | cal opens | raw P&L | cal P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| KMDW | 214 | 173 | 0.1738 | 0.1728 | -0.0009 | 175 | 169 | $-648.12 | $-980.71 |
| KMIA | 193 | 151 | 0.1784 | 0.1760 | -0.0024 | 168 | 169 | $-1504.60 | $-1597.93 |
| KNYC | 240 | 161 | 0.2046 | 0.1971 | -0.0075 | 175 | 183 | $-1303.29 | $-1461.09 |

## By Lead Day

| lead | n | calibrated | raw YES Brier | cal YES Brier | delta | raw opens | cal opens | raw P&L | cal P&L |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 228 | 165 | 0.1888 | 0.1877 | -0.0012 | 188 | 189 | $-836.63 | $-930.65 |
| 1 | 419 | 320 | 0.1853 | 0.1801 | -0.0053 | 330 | 332 | $-2619.38 | $-3109.08 |

## Interpretation

- Negative Brier delta is good: calibration improved probability accuracy.
- Lower calibrated opens is expected if the model was overconfident.
- P&L here ignores portfolio/open-position constraints. Treat it as an entry-quality stress test, not a trading ledger.