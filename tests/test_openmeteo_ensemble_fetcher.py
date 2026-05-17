from datetime import datetime, timezone

from weather_bot.config import Station
from weather_bot.data import openmeteo_ensemble_fetcher


class _Resp:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "hourly": {
                "time": ["2026-05-10T00:00", "2026-05-10T01:00"],
                "temperature_2m": [70.0, 71.0],
                "temperature_2m_member01": [69.0, None],
                "temperature_2m_member02": [72.0, 73.0],
            }
        }


def test_ensemble_fetcher_preserves_control_and_members(monkeypatch):
    seen = {}

    def fake_get(url, params, timeout):
        seen["url"] = url
        seen["params"] = params
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr("weather_bot.data.openmeteo_ensemble_fetcher.requests.get", fake_get)
    station = Station("KZZZ", "Test", 25.0, -80.0, "America/New_York")
    run_time = datetime(2026, 5, 10, 0, tzinfo=timezone.utc)

    rows = openmeteo_ensemble_fetcher.fetch_ensemble_tmp_series(
        station=station,
        model="GFS_ENS",
        openmeteo_model="gfs025",
        horizon_days=1,
        run_time=run_time,
    )

    assert seen["url"] == openmeteo_ensemble_fetcher.OM_ENSEMBLE_URL
    assert seen["params"]["models"] == "gfs025"
    assert seen["params"]["hourly"] == "temperature_2m"
    assert {r["member"] for r in rows} == {"control", "member01", "member02"}
    assert [r for r in rows if r["member"] == "member01"][0]["value"] == 69.0
    assert all(r["model"] == "GFS_ENS" for r in rows)
