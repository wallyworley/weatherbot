from datetime import datetime, timezone

from weather_bot.config import Station
from weather_bot.data import ecmwf_fetcher, gfs_fetcher


class _Resp:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "hourly": {
                "time": ["2026-05-10T00:00", "2026-05-10T01:00", "2026-05-10T02:00"],
                "temperature_2m": [70.0, None, 73.5],
            }
        }


def test_gfs_fetcher_writes_det_forecast_shape(monkeypatch):
    seen = {}

    def fake_get(url, params, timeout):
        seen["url"] = url
        seen["params"] = params
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr("weather_bot.data.openmeteo_det_fetcher.requests.get", fake_get)
    station = Station("KZZZ", "Test", 25.0, -80.0, "America/New_York")
    run_time = datetime(2026, 5, 10, 0, tzinfo=timezone.utc)

    rows = gfs_fetcher.fetch_gfs_tmp_series(station, horizon_days=1, run_time=run_time)

    assert seen["url"] == gfs_fetcher.OM_GFS_URL
    assert seen["params"]["temperature_unit"] == "fahrenheit"
    assert seen["params"]["timezone"] == "UTC"
    assert [r["model"] for r in rows] == ["GFS", "GFS"]
    assert rows[0]["lead_hr"] == 0
    assert rows[1]["lead_hr"] == 2
    assert rows[1]["value"] == 73.5


def test_ecmwf_fetcher_uses_ecmwf_model_label(monkeypatch):
    def fake_get(url, params, timeout):
        return _Resp()

    monkeypatch.setattr("weather_bot.data.openmeteo_det_fetcher.requests.get", fake_get)
    station = Station("KZZZ", "Test", 25.0, -80.0, "America/New_York")
    run_time = datetime(2026, 5, 10, 0, tzinfo=timezone.utc)

    rows = ecmwf_fetcher.fetch_ecmwf_tmp_series(station, horizon_days=1, run_time=run_time)

    assert rows
    assert {r["model"] for r in rows} == {"ECMWF"}
    assert {r["var"] for r in rows} == {"TMP_2M"}
