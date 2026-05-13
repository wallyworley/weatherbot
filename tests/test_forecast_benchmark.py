from datetime import date, datetime, timezone

from research.stored_forecast_benchmark import ForecastErrorRow, metric_summary


def test_metric_summary_reports_mae_rmse_bias():
    rows = [
        ForecastErrorRow("KNYC", "NBM", datetime(2026, 5, 1, tzinfo=timezone.utc), date(2026, 5, 2), 1, 70, 68, 2, 2),
        ForecastErrorRow("KNYC", "NBM", datetime(2026, 5, 2, tzinfo=timezone.utc), date(2026, 5, 3), 1, 66, 70, -4, 4),
    ]

    out = metric_summary(rows)

    assert out["n"] == 2
    assert out["mae"] == 3
    assert round(out["rmse"], 3) == 3.162
    assert out["bias"] == -1
