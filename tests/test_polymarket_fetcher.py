from datetime import date

from weather_bot.data import polymarket_fetcher


def test_parse_polymarket_temperature_buckets():
    assert polymarket_fetcher._parse_bucket("Will NYC be 65°F or below?") == (None, 66.0)
    assert polymarket_fetcher._parse_bucket("Will NYC be between 76-77°F on May 16?") == (76.0, 78.0)
    assert polymarket_fetcher._parse_bucket("Will NYC be 84°F or higher?") == (84.0, None)


def test_valid_date_from_event_slug():
    assert polymarket_fetcher._valid_date_from_slug("highest-temperature-in-nyc-on-may-16-2026") == date(2026, 5, 16)
