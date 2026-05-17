from datetime import date

from research.ai_context_brief import render_markdown


def test_ai_context_brief_renders_guardrails():
    text = render_markdown(
        "KNYC",
        date(2026, 5, 16),
        {
            "obs": {"cli_tmax_f": 80, "daily_tmax_f": 79, "daily_source": "METAR", "settled_tmax_f": 80},
            "nbm": [{"percentile": 50, "value": 81.2}],
            "markets": [{"ticker": "KXTEST", "lower_f": 80, "upper_f": 81, "status": "open"}],
            "signals": [{"ticker": "KXTEST", "fair_prob": 0.62, "market_ask": 0.55, "market_bid": 0.53, "action": "OPEN"}],
        },
    )

    assert "Weather Prediction Context Brief" in text
    assert "AI Review Guardrails" in text
    assert "KXTEST" in text
