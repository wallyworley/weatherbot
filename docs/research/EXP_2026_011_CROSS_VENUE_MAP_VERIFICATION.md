# EXP-2026-011 — Cross-Venue Same-Station Map: Verification Record (A2 expansion)

**Date:** 2026-06-09
**Scope:** amendment A2 anticipated map growth "until rules/source verified"; this document
is the required persisted citation record. Research-only; no production trading change.

## 1. Defect found and fixed: the channel was collecting nothing usable

Since instrumentation went live (2026-06-09 14:29 UTC), `polymarket_book` provenance had
been polling the **two hardcoded, long-resolved May 16 events** (`...-in-nyc-on-may-16-2026`,
`...-in-chicago-on-may-16-2026`) at stations **KLGA and KORD — both explicitly
non-comparable** under A2. 2,486 provenance rows collected with zero evidentiary value for
the channel. Left unfixed, the cross-venue channel would have had **no data at the
2026-06-23 evidence run**.

Fix (same day): `data/polymarket_fetcher.py` now generates today's + tomorrow's event slugs
dynamically for the verified-comparable city set below. Rows from the stale events are
excluded from scoring by the A2 map anyway (KLGA/KORD are not comparable stations).

## 2. Verified map (citations)

Method per A2: a pair is eligible only if the **Polymarket resolution source** (market
`description`, which names the Wunderground station page) maps to the **same physical
station** as the Kalshi market (verified from `kalshi_market.payload->rules_primary` in the
DB; our station codes were rules-verified at the 2026-05-26 graduation).

Polymarket rules read 2026-06-09 (June 10 events; the resolution text names the station and
links its Wunderground page):

| city (PM slug) | PM resolution station (from rules text + WU URL) | Kalshi station (rules_primary) | verdict |
|---|---|---|---|
| miami | Miami Intl — `.../KMIA` | KMIA | **comparable** (pre-existing) |
| atlanta | Hartsfield-Jackson — `.../KATL` | KATL | **comparable** (pre-existing) |
| austin | "Austin-Bergstrom International Airport Station" — `.../KAUS` | "Austin Bergstrom" (KAUS) | **comparable (NEW)** |
| seattle | "Seattle-Tacoma International Airport Station" — `.../KSEA` | "Seattle" (KSEA) | **comparable (NEW)** |
| los-angeles | "Los Angeles International Airport Station" — `.../KLAX` | "Los Angeles Airport" (KLAX) | **comparable (NEW)** |
| houston | "William P. Hobby Airport Station" — `.../KHOU` | "Houston" (KHOU Hobby) | **comparable (NEW)** |
| san-francisco | "San Francisco International Airport Station" — `.../KSFO` | "San Francisco" (KSFO) | **comparable (NEW)** |
| dallas | "Dallas Love Field Station" — `.../KDAL` | "Dallas" (KDFW Intl) | **NOT comparable (NEW exclusion)** |
| nyc | LaGuardia (KLGA) | KNYC Central Park | not comparable (pre-existing) |
| chicago | O'Hare (KORD) | KMDW Midway | not comparable (pre-existing) |
| denver | Buckley SFB | KDEN Intl | not comparable (pre-existing) |

No Polymarket daily-temp event found 2026-06-09 for: Phoenix, Boston, Las Vegas,
Minneapolis, New Orleans, Oklahoma City, San Antonio, Philadelphia, Washington (searched
gamma `public-search`, "Highest temperature {city} on June 10").

**Comparable set: 7 stations (was 2).** Standing source-basis caveat unchanged: Polymarket
settles Wunderground raw obs, Kalshi settles NWS CLI — same-station gaps can still be
basis, not lag; the A2 scoring rules already account for this.

## 3. Sample-velocity consequence

The §7 candidate gate needs >= 100 event-days on genuine forward data. At 2 comparable
stations that was ~50 calendar days (late July); at 7 it is **~15 days**, putting the
cross-venue channel inside the 2026-06-23 evidence-run window (or shortly after) instead of
a month past it. Forward collection on the expanded set starts 2026-06-10 (first full day).

## 4. What was and was not changed

- Changed (research-only collection): `data/polymarket_fetcher.py` slug generation + station
  map; `research/polymarket_crosscheck.py` PM_CITY verified entries.
- Not changed: production probabilities, sizing, gating, execution, station activation;
  the locked A2 scoring rules; the locked lag statistic and candidate gate.
- The pre-2026-06-10 KLGA/KORD provenance rows remain in the table (additive store) but are
  outside the comparable map and excluded from scoring by A2.
