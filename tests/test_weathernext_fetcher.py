from datetime import date, datetime, timezone

from research.sources import weathernext_fetcher as wn


def test_weather_next_daily_member_extrema_and_quantiles():
    hours = [
        wn.WeatherNextHour(datetime(2026, 5, 10, 5, tzinfo=timezone.utc), "0", 70.0),
        wn.WeatherNextHour(datetime(2026, 5, 10, 18, tzinfo=timezone.utc), "0", 82.0),
        wn.WeatherNextHour(datetime(2026, 5, 10, 5, tzinfo=timezone.utc), "1", 68.0),
        wn.WeatherNextHour(datetime(2026, 5, 10, 18, tzinfo=timezone.utc), "1", 86.0),
        wn.WeatherNextHour(datetime(2026, 5, 11, 2, tzinfo=timezone.utc), "1", 90.0),
    ]

    tmax, tmin = wn.daily_member_extrema(hours, date(2026, 5, 10), "UTC")
    summary = wn.summarize_member_values(tmax)

    assert tmax == {"0": 82.0, "1": 86.0}
    assert tmin == {"0": 70.0, "1": 68.0}
    assert summary["members"] == 2
    assert summary["p50_f"] == 84.0
    assert summary["mean_f"] == 84.0


def test_weather_next_fails_softly_when_table_not_configured(monkeypatch):
    monkeypatch.delenv("WEATHERNEXT_BQ_TABLE", raising=False)
    monkeypatch.delenv("WEATHERNEXT_BQ_PROJECT", raising=False)
    monkeypatch.delenv("WEATHERNEXT_BQ_DATASET", raising=False)

    out = wn.fetch_forecast_daily("KNYC", date(2026, 5, 10), fail_soft=True)

    assert out["model"] == "WeatherNext2"
    assert out["available"] is False
    assert out["members"] == 0
    assert "WeatherNext is not configured" in out["error"]
