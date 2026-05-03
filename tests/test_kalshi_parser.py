"""Unit tests for Kalshi market parsing."""
from datetime import date

from weather_bot.strategy import kalshi_parser


def test_event_ticker_high_ny():
    evt = kalshi_parser.parse_event_ticker("KXHIGHNY-26APR18")
    assert evt is not None
    assert evt["var"] == "TMAX_DAILY"
    assert evt["station"] == "KNYC"
    assert evt["valid_date"] == date(2026, 4, 18)


def test_event_ticker_low_chi():
    evt = kalshi_parser.parse_event_ticker("KXLOWCHI-26APR18")
    assert evt["var"] == "TMIN_DAILY"
    # Kalshi CHI markets resolve on Chicago Midway (KMDW), not O'Hare.
    # Verified 2026-05-02 from market payload rules_primary.
    assert evt["station"] == "KMDW"


def test_bucket_range():
    assert kalshi_parser.parse_bucket("65-66°F") == (65.0, 67.0)


def test_bucket_above():
    assert kalshi_parser.parse_bucket("70°F or above") == (70.0, None)


def test_bucket_below():
    lo, hi = kalshi_parser.parse_bucket("50°F or below")
    assert lo is None
    assert hi == 51.0


def test_bucket_exactly():
    assert kalshi_parser.parse_bucket("exactly 72°F") == (72.0, 73.0)


def test_parse_market_full_payload():
    payload = {
        "ticker": "KXHIGHNY-26APR18-T68",
        "event_ticker": "KXHIGHNY-26APR18",
        "yes_sub_title": "67-68°F",
        "status": "open",
    }
    row = kalshi_parser.parse_market(payload)
    assert row["station"] == "KNYC"
    assert row["var"] == "TMAX_DAILY"
    assert row["lower_f"] == 67.0
    assert row["upper_f"] == 69.0
