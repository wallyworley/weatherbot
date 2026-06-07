# WeatherBot Document Status

**Date:** 2026-06-07

This index records which documents are authoritative after adding the
market-relative research charter. It is intentionally conservative: older audit
documents are retained when they provide traceability, but they no longer govern
promotion or research priority.

## Authoritative Research Documents

| Document | Status | Reason |
|---|---|---|
| `docs/research/WEATHERBOT_RESEARCH_CHARTER.md` | Keep / canonical | Defines current scientific program and non-goals. |
| `docs/research/MARKET_BASELINE_THESIS.md` | Keep / canonical | Defines the market-implied forecast as the benchmark to beat. |
| `docs/research/WEATHERBOT_PROMOTION_CRITERIA.md` | Keep / canonical | Defines requirements before research can affect probabilities, sizing, or trading. |
| `docs/research/WEATHERBOT_EXPERIMENT_REGISTRY.md` | Keep / canonical | Pre-registration log to prevent overfitting. |
| `docs/research/WEATHERBOT_PRIORITY_BACKLOG.md` | Keep / canonical | Current prioritized research backlog. |
| `docs/research/MARKET_RELATIVE_CENTER_BENCHMARK_2026_06_07.md` | Keep / canonical input | Current market-relative forecast-center benchmark report. |
| `docs/research/CLI_RESEARCH_PROMPT.md` | Keep | Reproducible prompt for future benchmark/defect audits. |

## Current Operational Documents

| Document | Status | Reason |
|---|---|---|
| `README.md` | Keep / updated | Primary repo overview; now points to `docs/research/` for research authority. |
| `RUNBOOK.md` | Keep / operational only | Still useful for deployed paper-bot operations, but no longer promotion authority. |
| `setup.md` | Keep / updated | Useful setup instructions; checklist now requires market-relative forecast skill. |

## Historical Or Superseded Documents To Retain

| Document | Status | Reason |
|---|---|---|
| `docs/bot_performance_evaluation_2026_06_04.md` | Keep / historical | Important evidence path leading to the market-relative benchmark. |
| `docs/forecast_model_roadmap_2026_06_05.md` | Keep / historical roadmap | Contains source/retriever context and model-source notes still useful for research. |
| `docs/review_followups_2026_06_09.md` | Keep / historical | Captures corrected external-trader interpretation and follow-up context. |
| `docs/review_2026_05_29_opus48.md` | Keep / historical | Documents winner's-curse diagnosis and changes shipped then. |
| `docs/independent_review_2026_05_29.md` | Keep / historical | Independent review trail for May calibration/settlement work. |
| `docs/handoff_for_next_model_2026_05_29.md` | Keep / historical | Useful for reconstructing May state, but not current guidance. |
| `docs/audit_2026_05_28.md` | Keep / historical | Settlement/dashboard audit trail. |
| `docs/fix_plan_2026_05_28.md` | Keep / historical | Settlement hotfix plan traceability. |
| `docs/followup_plan_2026_05_28.md` | Keep / historical | Deferred settlement/dashboard follow-ups. |
| `docs/history/*.md` | Keep / archive | Already marked as historical context. |
| `BACKLOG.md` | Keep / superseded | Retained for older technical backlog; prioritization moved to `docs/research/WEATHERBOT_PRIORITY_BACKLOG.md`. |

## Not Added

| Source file | Decision | Reason |
|---|---|---|
| `README_FOR_REPO_ADD.md` from the download bundle | Do not add | Packaging instructions for this import, not durable project documentation. Its actionable locations were applied here. |

## Removal Decision

No existing repo documents were removed in this pass. The older files contain
audit history and operational context that would be hard to reconstruct. Where
they conflict with `docs/research/`, the `docs/research/` documents win.
