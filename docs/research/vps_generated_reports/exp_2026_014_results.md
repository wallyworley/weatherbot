# EXP-2026-014 — Kalshi Market Self-Calibration (design set) — 2026-06-09

_generated 2026-06-09 23:39 UTC_

Locked prereg: `EXP_2026_014_MARKET_SELF_CALIBRATION.md`. Market-only study;
the bot's forecast is never an input. Primary = buy 1 YES of the highest-mid
bucket at the ASK iff mid >= 0.5, hold to settlement, taker fee
included. CIs are cluster-bootstrap by (station, valid_date), seed 1337.

Universe: 1968 scored bucket rows through 2026-06-08 (6 excluded for missing CLI truth).

## Primary (LOCKED): morning favorite at the ask, fees in

| slice | n | win rate | net P&L / contract | 95% cluster CI |
|---|---:|---:|---:|---|
| ALL (primary) | 195 | 0.651 | +0.0083 | [-0.0564, +0.0753] |
| first half (< 2026-05-20) | 33 | 0.697 | +0.0700 | [-0.0930, +0.2239] |
| second half (>= 2026-05-20) | 162 | 0.642 | -0.0043 | [-0.0767, +0.0667] |
| TMAX_DAILY | 193 | 0.648 | +0.0071 | [-0.0574, +0.0703] |
| TMIN_DAILY | 2 | 1.000 | +0.1250 | [+0.1000, +0.1500] |
| spread <= 0.1 (diagnostic) | 192 | 0.646 | +0.0053 | [-0.0611, +0.0675] |
| fee-free (diagnostic) | 195 | 0.651 | +0.0277 | [-0.0369, +0.0920] |
| mid fill, no fee (diagnostic) | 195 | 0.651 | +0.0408 | [-0.0224, +0.1024] |

## Per-station (>=10 events): 4/7 positive (57% — design pass needs >=60%)

| station | n | mean net P&L |
|---|---:|---:|
| KLAS | 13 | +0.1377 |
| KLAX | 13 | +0.1454 |
| KMDW | 19 | -0.0484 |
| KMIA | 25 | +0.0532 |
| KNYC | 35 | +0.0629 |
| KPHL | 10 | -0.0910 |
| KPHX | 13 | -0.0462 |

## Decile calibration (diagnostic; all buckets; edge = win rate − mid)

| decile | n | avg mid | win rate | edge | 95% cluster CI (edge) |
|---|---:|---:|---:|---:|---|
| 0.0–0.1 | 1109 | 0.023 | 0.017 | -0.0058 | [-0.0127, +0.0021] |
| 0.1–0.2 | 190 | 0.146 | 0.121 | -0.0245 | [-0.0690, +0.0221] |
| 0.2–0.3 | 165 | 0.248 | 0.188 | -0.0602 | [-0.1169, -0.0034] |
| 0.3–0.4 | 165 | 0.347 | 0.400 | +0.0531 | [-0.0123, +0.1288] |
| 0.4–0.5 | 144 | 0.447 | 0.431 | -0.0160 | [-0.0885, +0.0536] |
| 0.5–0.6 | 115 | 0.547 | 0.565 | +0.0180 | [-0.0720, +0.1040] |
| 0.6–0.7 | 48 | 0.639 | 0.708 | +0.0692 | [-0.0589, +0.1914] |
| 0.7–0.8 | 23 | 0.745 | 0.826 | +0.0813 | [-0.0959, +0.2196] |
| 0.8–0.9 | 3 | 0.863 | 1.000 | +0.1367 | [+0.1050, +0.1800] |
| 0.9–1.0 | 6 | 0.953 | 1.000 | +0.0475 | [+0.0325, +0.0658] |

## Longshot NO side (diagnostic): buy NO at ask on mid in [0.1, 0.3)

| slice | n | win rate | net P&L / contract | 95% cluster CI |
|---|---:|---:|---:|---|
| buy NO on longshots | 342 | 0.842 | +0.0086 | [-0.0251, +0.0440] |

Design-pass requires (prereg §7): primary CI excluding 0 AND both halves positive
AND >=60% stations positive. A pass only opens the forward window
(valid_date >= 2026-06-10, >=300 fresh events). No production change; nothing trades.
