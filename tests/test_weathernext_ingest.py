from datetime import datetime, timezone

from weather_bot.data import weathernext_fetcher
from research.sources.weathernext_fetcher import WeatherNextHour


def test_weathernext_fetcher_writes_ensemble_shape(monkeypatch):
    run_time = datetime(2026, 5, 16, 0, tzinfo=timezone.utc)

    def fake_fetch(station, init_time, table=None):
        assert station == "KNYC"
        assert init_time == run_time
        assert table == "project.dataset.weathernext_2_0_0"
        return [
            WeatherNextHour(datetime(2026, 5, 16, 1, tzinfo=timezone.utc), "0", 70.0),
            WeatherNextHour(datetime(2026, 5, 16, 2, tzinfo=timezone.utc), "1", 72.0),
            WeatherNextHour(datetime(2026, 5, 25, 0, tzinfo=timezone.utc), "1", 80.0),
        ]

    monkeypatch.setattr(weathernext_fetcher.weathernext_fetcher, "fetch_hourly_ensemble", fake_fetch)

    rows = weathernext_fetcher.fetch_weathernext_tmp_series(
        "KNYC",
        run_time=run_time,
        horizon_days=1,
        table="project.dataset.weathernext_2_0_0",
    )

    assert len(rows) == 2
    assert {r["model"] for r in rows} == {"WEATHERNEXT2"}
    assert {r["member"] for r in rows} == {"0", "1"}
    assert rows[0]["lead_hr"] == 1
    assert rows[1]["value"] == 72.0
