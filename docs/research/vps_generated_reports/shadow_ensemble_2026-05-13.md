# Shadow Ensemble Replay — 2026-05-13

_generated 2026-05-13 12:14 UTC_

Window: last 30 completed valid dates. This is research-only; it does not affect trading.
Shadow P&L is an unconstrained replay of every eligible signal, so use Brier/calibration first and dollars only as a rough stress test.

## Overall

| n | original Brier | shadow Brier | delta | shadow opens | shadow P&L |
|---:|---:|---:|---:|---:|---:|
| 1200 | 0.1090 | 0.1369 | +0.0279 | 1023 | $+102018.51 |

## By station / lead

| station | lead | n | original Brier | shadow Brier | delta | shadow P&L |
|---|---:|---:|---:|---:|---:|---:|
| KMDW | 0 | 200 | 0.0938 | 0.1886 | +0.0948 | $-3523.27 |
| KMDW | 1 | 200 | 0.1498 | 0.1366 | -0.0133 | $-1578.46 |
| KMIA | 0 | 200 | 0.0963 | 0.1821 | +0.0857 | $-3683.07 |
| KMIA | 1 | 200 | 0.1589 | 0.2021 | +0.0432 | $-1097.57 |
| KNYC | 0 | 200 | 0.1000 | 0.0284 | -0.0715 | $+110168.14 |
| KNYC | 1 | 200 | 0.0553 | 0.0835 | +0.0283 | $+1732.74 |

## Promotion rule

- Keep this shadow-only until it beats original Brier on at least 50 settled signals.
- Then replay fixed-size P&L and reliability bins before using it in `main.py`.
