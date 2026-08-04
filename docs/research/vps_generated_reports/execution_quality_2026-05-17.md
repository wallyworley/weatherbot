# Execution Quality Report - 2026-05-17

_generated 2026-05-17 00:42 UTC_

Window: last 45 days. Research-only; paper fills assume immediate top-of-book execution.

## Summary

| metric | value |
|---|---:|
| fills | 203 |
| settled fills | 173 |
| fills with prior book snapshot | 198 |
| avg prior snapshot age | 864.2 sec |
| avg book ask - fill price | 0.0107 |
| top-of-book too small for paper fill | 0 |
| avg 15m mark-to-market bid edge | -0.0149 |
| avg 30m mark-to-market bid edge | -0.0157 |
| avg 60m mark-to-market bid edge | -0.0113 |

## Low-price convexity sleeve

| side | price band | n | win rate | P&L | $/fill | avg contracts |
|---|---|---:|---:|---:|---:|---:|
| NO | 10-25c | 2 | 0.0% | $-31.00 | $-15.50 | 101.0 |
| NO | 25-50c | 7 | 0.0% | $-123.11 | $-17.59 | 42.0 |
| NO | 50-75c | 35 | 65.7% | $-40.91 | $-1.17 | 28.4 |
| NO | 75c+ | 49 | 87.8% | $-11.50 | $-0.23 | 21.2 |
| NO | <10c | 1 | 0.0% | $-10.00 | $-10.00 | 312.0 |
| YES | 10-25c | 30 | 20.0% | $+59.13 | $+1.97 | 99.8 |
| YES | 25-50c | 13 | 38.5% | $-48.27 | $-3.71 | 37.0 |
| YES | 50-75c | 1 | 0.0% | $-19.88 | $-19.88 | 28.0 |
| YES | 75c+ | 1 | 100.0% | $+5.98 | $+5.98 | 26.0 |
| YES | <10c | 34 | 2.9% | $-178.06 | $-5.24 | 395.3 |

## Forecast-update age buckets

| source | age bucket | settled n | P&L | $/fill |
|---|---|---:|---:|---:|
| NBM | <15m | 17 | $-84.57 | $-4.97 |
| NBM | 15-60m | 26 | $-249.48 | $-9.60 |
| NBM | 1-3h | 6 | $-127.95 | $-21.32 |
| NBM | 3-6h | 61 | $-303.15 | $-4.97 |
| NBM | 6h+ | 3 | $+93.84 | $+31.28 |
| NBM | unknown | 60 | $+273.69 | $+4.56 |
| HRRR | <15m | 42 | $-41.77 | $-0.99 |
| HRRR | 15-60m | 97 | $-148.31 | $-1.53 |
| HRRR | 1-3h | 7 | $+44.13 | $+6.30 |
| HRRR | 6h+ | 27 | $-251.67 | $-9.32 |
| GFS | <15m | 3 | $+28.28 | $+9.43 |
| GFS | 15-60m | 5 | $-33.09 | $-6.62 |
| GFS | 1-3h | 25 | $-170.30 | $-6.81 |
| GFS | 3-6h | 43 | $-311.23 | $-7.24 |
| GFS | 6h+ | 17 | $-45.91 | $-2.70 |
| GFS | unknown | 80 | $+134.63 | $+1.68 |
| ECMWF | 15-60m | 1 | $-10.00 | $-10.00 |
| ECMWF | 1-3h | 3 | $-39.58 | $-13.19 |
| ECMWF | unknown | 169 | $-348.04 | $-2.06 |

## Interpretation

- Low-price bands are where convexity should show up: many small losses need occasional large wins to pay for them.
- Positive 15m/30m/60m mark-to-market means the orderbook moved in our direction after the paper fill; negative means we were early or crossed too much spread.
- Cross-platform gaps are not backtestable yet because no second-venue price feed is logged.
