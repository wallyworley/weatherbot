"""Regression tests for NWS CLI parsing.

Each fixture in tests/fixtures/cli_<station>_<for_date>_<phase>.txt is a real
NWS product captured from the API. To refresh:

    curl -s "https://api.weather.gov/products/<product_id>" \\
        -H "User-Agent: weather_bot/test" \\
        | python3 -c "import json,sys; print(json.load(sys.stdin)['productText'])" \\
        > tests/fixtures/cli_<station>_<for_date>_<phase>.txt

Cases below intentionally include the failure mode that motivated the
2026-05-28 fix: a CLI body whose header line contains "VALID TODAY AS OF
0500 PM LOCAL TIME" used to make the parser return None because the
\\bTODAY\\b regex anchored on the wrong word.
"""
from pathlib import Path

from weather_bot.data import nws_text_products as nws

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_evening_cli_with_valid_today_header_in_body():
    """KAUS 5/27 evening CLI: header contains 'VALID TODAY AS OF 0500 PM
    LOCAL TIME'. Parser must locate the real TODAY section and extract the
    PM maximum, not return None because of the header-line false match."""
    obs = nws.parse_cli(_read("cli_kaus_2026-05-27_evening.txt"))
    assert obs.section == "TODAY"
    assert obs.tmax_f == 77.0
    assert obs.tmax_time_lst == "4:44 PM"


def test_morning_intraday_cli_overnight_only():
    """KAUS 5/27 morning CLI: only overnight data available; the only
    'MAXIMUM' value is the overnight high at 3:42 AM. Parser should still
    extract it (the outer _select_cli_for_target is what filters AM times
    as unfinished, not parse_cli itself)."""
    obs = nws.parse_cli(_read("cli_kaus_2026-05-27_morning.txt"))
    assert obs.section == "TODAY"
    assert obs.tmax_f == 65.0
    assert obs.tmax_time_lst == "3:42 AM"


def test_morning_after_yesterday_cli():
    """KNYC 5/27 morning CLI reporting 5/26 final data in YESTERDAY
    section. Body title is 'CLIMATE SUMMARY FOR MAY 26 2026', which is
    the *target* date, not target+1. The for_date check in
    _select_cli_for_target was incorrectly requiring target+1; verify
    parse_cli alone returns the right data and (after the fix) the
    selector accepts it."""
    obs = nws.parse_cli(_read("cli_knyc_2026-05-26.txt"))
    assert obs.section == "YESTERDAY"
    assert obs.tmax_f == 79.0
    assert "PM" in (obs.tmax_time_lst or "")


def test_extract_for_date_from_evening_cli():
    """Verify the FOR-date extraction also works on a body where the title
    says 'FOR MAY 27 2026' (KAUS evening case)."""
    from datetime import date
    d = nws._extract_for_date(_read("cli_kaus_2026-05-27_evening.txt"))
    assert d == date(2026, 5, 27)


def test_extract_for_date_from_morning_after_cli():
    """KNYC fixture title says 'FOR MAY 26 2026' — the previous day, not
    the issuance day. This is the reviewer's flagged pattern."""
    from datetime import date
    d = nws._extract_for_date(_read("cli_knyc_2026-05-26.txt"))
    assert d == date(2026, 5, 26)


def test_ksat_evening_kewx_peer():
    """KSAT same WFO as KAUS (KEWX) — also has VALID TODAY in header.
    Should extract 84°F at 4:26 PM TODAY."""
    obs = nws.parse_cli(_read("cli_ksat_2026-05-27_evening.txt"))
    assert obs.section == "TODAY"
    assert obs.tmax_f == 84.0
    assert obs.tmax_time_lst == "4:26 PM"


def test_kden_evening_kbou_pattern():
    """KDEN (KBOU) — same evening-intraday-only pattern. Header has
    VALID TODAY AS OF 0400 PM. Should extract 70°F at 1:30 PM TODAY."""
    obs = nws.parse_cli(_read("cli_kden_2026-05-27_evening.txt"))
    assert obs.section == "TODAY"
    assert obs.tmax_f == 70.0
    assert obs.tmax_time_lst == "130 PM"


def test_kmia_morning_after_normal():
    """KMIA (KMFL) — typical normal pattern. Title 'FOR MAY 26 2026' with
    clean YESTERDAY section. Should extract 88°F at 3:15 PM YESTERDAY."""
    obs = nws.parse_cli(_read("cli_kmia_2026-05-26.txt"))
    assert obs.section == "YESTERDAY"
    assert obs.tmax_f == 88.0
    assert obs.tmax_time_lst == "3:15 PM"


def test_ksfo_morning_after_pacific():
    """KSFO Pacific-time station. Morning-after CLI with YESTERDAY section.
    Should extract 64°F at 12:59 PM YESTERDAY."""
    obs = nws.parse_cli(_read("cli_ksfo_2026-05-26.txt"))
    assert obs.section == "YESTERDAY"
    assert obs.tmax_f == 64.0
    assert obs.tmax_time_lst == "12:59 PM"
