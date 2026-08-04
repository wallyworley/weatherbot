# Market Information Forensics - 2026-06-08

_generated 2026-06-08 22:37 UTC_

Window: last 30 days; current valid date included: `False`.

Research-only. The authoritative run target is the VPS PostgreSQL database.

## Coverage

- snapshot rows: 70617
- station-days: 315
- stations: 20

## Station Evidence

| station | snapshots | avg METAR age min | abs 10m move | abs next 10m move | boundary <=0.5F | CLI rows | DSM rows |
|---|---:|---:|---:|---:|---:|---:|---:|
| KATL | 3025 | 4.2 | 0.062 | 0.060 | 444 | 3025 | 0 |
| KAUS | 3100 | 4.2 | 0.060 | 0.061 | 415 | 3100 | 0 |
| KBOS | 3021 | 4.6 | 0.075 | 0.073 | 568 | 3021 | 0 |
| KDCA | 3025 | 4.2 | 0.054 | 0.054 | 521 | 3025 | 0 |
| KDEN | 3184 | 4.2 | 0.052 | 0.050 | 451 | 3184 | 0 |
| KDFW | 3108 | 4.2 | 0.065 | 0.064 | 329 | 3108 | 0 |
| KHOU | 86 | 4.4 | 0.072 | 0.074 | 77 | 86 | 0 |
| KLAS | 3269 | 4.1 | 0.035 | 0.034 | 458 | 3269 | 0 |
| KLAX | 3266 | 4.2 | 0.049 | 0.049 | 723 | 3266 | 0 |
| KMDW | 6907 | 3.3 | 0.071 | 0.069 | 1287 | 6907 | 0 |
| KMIA | 6732 | 3.4 | 0.052 | 0.052 | 951 | 6498 | 0 |
| KMSP | 3104 | 4.6 | 0.054 | 0.053 | 499 | 3104 | 0 |
| KMSY | 3104 | 4.5 | 0.056 | 0.056 | 664 | 3104 | 0 |
| KNYC | 6731 | 23.6 | 0.055 | 0.056 | 1162 | 6731 | 0 |
| KOKC | 3104 | 4.2 | 0.071 | 0.071 | 501 | 3104 | 0 |
| KPHL | 3017 | 4.2 | 0.056 | 0.055 | 699 | 3017 | 0 |
| KPHX | 3190 | 4.2 | 0.036 | 0.036 | 500 | 3190 | 0 |
| KSAT | 3104 | 4.2 | 0.063 | 0.063 | 609 | 3104 | 0 |
| KSEA | 3270 | 4.2 | 0.054 | 0.053 | 370 | 3270 | 0 |
| KSFO | 3270 | 4.1 | 0.064 | 0.063 | 674 | 3270 | 0 |

## Latency: Latest METAR Age

| latest METAR age | snapshots | avg METAR age min | abs 10m move | abs next 10m move | boundary <=0.5F | CLI rows | DSM rows |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0-5m | 40290 | 4.0 | 0.060 | 0.059 | 10686 | 40140 | 0 |
| 5-10m | 1674 | 8.1 | 0.066 | 0.049 | 456 | 1674 | 0 |
| 10-30m | 1536 | 22.4 | 0.048 | 0.049 | 405 | 1536 | 0 |
| 30-60m | 1369 | 43.1 | 0.049 | 0.069 | 355 | 1369 | 0 |
| none | 25748 | - | 0.054 | 0.054 | 0 | 25664 | 0 |

## Boundary States

| distance to boundary | snapshots | avg METAR age min | abs 10m move | abs next 10m move | boundary <=0.5F | CLI rows | DSM rows |
|---|---:|---:|---:|---:|---:|---:|---:|
| <=0.25F | 7630 | 7.0 | 0.055 | 0.049 | 7630 | 7621 | 0 |
| 0.25-0.5F | 4272 | 4.0 | 0.052 | 0.046 | 4272 | 4272 | 0 |
| 0.5-1.0F | 12060 | 5.6 | 0.060 | 0.059 | 0 | 11920 | 0 |
| >1.0F | 20898 | 6.2 | 0.062 | 0.063 | 0 | 20897 | 0 |
| none | 25757 | 4.6 | 0.054 | 0.054 | 0 | 25673 | 0 |

## Timing Labels

| label | snapshots | avg METAR age min | abs 10m move | abs next 10m move | boundary <=0.5F | CLI rows | DSM rows |
|---|---:|---:|---:|---:|---:|---:|---:|
| market_already_repriced_after_metar | 444 | 31.9 | 0.198 | 0.140 | 112 | 444 | 0 |
| market_moved_around_recent_metar | 2990 | 4.1 | 0.255 | 0.265 | 791 | 2965 | 0 |
| market_moved_before_or_at_recent_metar | 4227 | 4.2 | 0.247 | 0.041 | 857 | 4219 | 0 |
| market_moves_after_recent_metar | 4059 | 4.1 | 0.041 | 0.227 | 729 | 4050 | 0 |
| no_live_metar | 25748 | - | 0.054 | 0.054 | 0 | 25664 | 0 |
| no_material_move_after_recent_metar | 30688 | 4.1 | 0.018 | 0.018 | 8765 | 30580 | 0 |
| recent_metar_no_material_market_move | 2461 | 32.2 | 0.021 | 0.043 | 648 | 2461 | 0 |

## Current Recommendation

No paper-only candidate signal is promoted by this report alone. Use the CSV to pre-register a latency or boundary-state signal, then test it out of sample against market-relative Brier and RPS with paired confidence intervals.
