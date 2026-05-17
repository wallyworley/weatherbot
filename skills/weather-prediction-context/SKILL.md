---
name: weather-prediction-context
description: Use when reviewing Weather Bot station/date predictions, forecast edge, market lag, settlement context, or whether qualitative weather-market context supports or warns against a numeric trade signal.
---

# Weather Prediction Context

Use this skill only as an advisory review layer. Do not create, size, cancel, or
recommend orders unless the deterministic bot already has positive fee-aware EV.

## Workflow

1. Generate a deterministic context brief:

```bash
.venv/bin/python -m weather_bot.jobs.ai_context_brief --station KNYC --valid-date YYYY-MM-DD
```

2. Review the brief for non-numeric context:

- Settlement station/date mismatch.
- Stale Kalshi or Polymarket prices.
- Fresh forecast run jumps not reflected in market prices.
- CLI/daily_obs conflicts or missing settlement data.
- Bucket boundary risk near the current observed or forecast high.
- Ensemble spread or station-specific bias that contradicts a simple point forecast.

3. Return one of three labels:

- `context_supports`: qualitative context agrees with the numeric edge.
- `context_warns`: context suggests reducing confidence or waiting.
- `insufficient_context`: context is too thin, stale, or contradictory.

## Guardrails

- The skill is context only. It never overrides health checks, bias gates, fee
  filters, book-depth constraints, or PAPER_MODE.
- Prefer fewer words and explicit evidence. Mention the rows or fields that
  caused the label.
- If a conclusion depends on current public web information, verify it before
  using it.
