from datetime import datetime, timezone

from weather_bot.jobs import backfill_weathernext


def test_historical_six_hour_cycles_descending():
    cycles = backfill_weathernext.historical_six_hour_cycles(
        days_back=1,
        now=datetime(2026, 5, 16, 14, 30, tzinfo=timezone.utc),
    )

    assert cycles == [
        datetime(2026, 5, 16, 12, tzinfo=timezone.utc),
        datetime(2026, 5, 16, 6, tzinfo=timezone.utc),
        datetime(2026, 5, 16, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 15, 18, tzinfo=timezone.utc),
    ]


def test_backfill_continues_when_cycle_fails(monkeypatch):
    cycles = [
        datetime(2026, 5, 16, 12, tzinfo=timezone.utc),
        datetime(2026, 5, 16, 6, tzinfo=timezone.utc),
        datetime(2026, 5, 16, 0, tzinfo=timezone.utc),
    ]
    calls = []

    monkeypatch.setattr(backfill_weathernext, "historical_six_hour_cycles", lambda days_back: cycles)

    def fake_run(**kwargs):
        calls.append(kwargs)
        if kwargs["cycle"] == cycles[1]:
            raise RuntimeError("missing cycle")
        return 12

    monkeypatch.setattr(backfill_weathernext.weathernext_fetcher, "run", fake_run)

    result = backfill_weathernext.run(
        days_back=1,
        stations=["KNYC"],
        horizon_days=2,
        max_cycles=3,
    )

    assert [c["cycle"] for c in calls] == cycles
    assert all(c["stations"] == ["KNYC"] for c in calls)
    assert all(c["horizon_days"] == 2 for c in calls)
    assert result == {"cycles": 3, "nonzero_cycles": 2, "failures": 1, "rows": 24}
