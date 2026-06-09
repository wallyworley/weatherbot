from datetime import date, datetime, timezone

from weather_bot.data import persistence


def test_forecast_run_provenance_dedupes_by_station_model_run(monkeypatch):
    captured = []

    def fake_record(rows):
        captured.extend(list(rows))

    monkeypatch.setattr(persistence, "record_info_provenance", fake_record)
    run_time = datetime(2026, 6, 9, 12, tzinfo=timezone.utc)

    persistence._record_forecast_run_provenance(
        [
            {
                "station": "KNYC",
                "model": "NBM_QMD",
                "run_time": run_time,
                "valid_date": date(2026, 6, 9),
                "var": "TMAX_DAILY",
                "percentile": 50,
                "value": 80.0,
            },
            {
                "station": "KNYC",
                "model": "NBM_QMD",
                "run_time": run_time,
                "valid_date": date(2026, 6, 10),
                "var": "TMAX_DAILY",
                "percentile": 90,
                "value": 84.0,
            },
            {
                "station": "KLAX",
                "model": "NBM_QMD",
                "run_time": run_time,
                "valid_date": date(2026, 6, 9),
                "var": "TMAX_DAILY",
                "percentile": 50,
                "value": 70.0,
            },
        ]
    )

    assert len(captured) == 2
    knyc = next(row for row in captured if row["station"] == "KNYC")
    assert knyc["source_type"] == "nbm_run"
    assert knyc["official_ts"] == run_time
    assert knyc["event_key"] == f"KNYC|NBM_QMD|{run_time}"
    assert knyc["value_summary"]["row_count"] == 2
    assert knyc["value_summary"]["vars"] == {"TMAX_DAILY"}
    assert knyc["value_summary"]["valid_dates"] == {date(2026, 6, 9), date(2026, 6, 10)}

