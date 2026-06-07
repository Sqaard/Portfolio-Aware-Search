# Current Artifacts And Experiments

Status date: 2026-05-15.

This file tracks current datasets, exports, experiment results, and caveats. It
replaces older one-off status notes.

## Daily Retrieval Package

Current validated daily retrieval package:

```text
data/exports/daily_retrieval_ppo_full_dis_legacy/retrieved_contexts.jsonl
```

Known validated properties:

| Metric | Value |
|---|---:|
| Contexts | 28,493 |
| Unique documents | 2,141 |
| Portfolio contexts | 16,550 |
| Stock contexts | 11,943 |
| Official macro contexts | 16,550 |
| SEC section contexts | 8,733 |
| SEC exhibit contexts | 3,210 |
| Unique decision dates | 3,310 |
| Unique stock tickers | 29 |
| Train contexts | 25,447 |
| Test contexts | 3,046 |
| Strict leakage rows | 0 |
| Hard validation issues | 0 |

Merge-ready panel:

```text
data/exports/daily_retrieval_ppo_full_dis_legacy/rl_panel_codex_rule_text_features.csv
```

PPO ablation package:

```text
data/exports/daily_retrieval_ppo_full_dis_legacy/ppo_ablation_package/
```

## Trusted Data Package

Current trusted source package:

```text
data/exports/trusted_source_data_package_2026_05_14.zip
```

Counts:

| Bucket | Count |
|---|---:|
| Trusted domains | 32 |
| Trusted source labels | 38 |
| Quality/review document units | 20,005 |
| Official macro observations | 18,240 |
| SEC filing exhibits | 655 |
| Company IR core documents | 844 |
| Company IR review documents | 266 |
| Excluded/not-core official or noisy units | 6,363 |

The package is a data handoff package. Internal QA folders, source-quality
audits, macro-rule code, and not-core manifests are intentionally separate.

## Full Document/Context Universe

Current source-quality audit universe:

| Source family | Contexts | Role |
|---|---:|---|
| Official macro | 16,550 | Dense portfolio-level macro/rates/credit context |
| SEC EDGAR | 11,196 | Company filing sections and exhibits |
| Company IR | 747 | Official company releases and reports |

Company IR is now included in the daily retrieval universe, but it still needs
route-aware/source-family slots to avoid competing too aggressively with SEC
sections for the same single stock slot.

## Codex-Rule Text Features

`codex_rule_text_features` is deterministic/rule-based extraction. It is not
Mistral, not FinGPT inference, and not final truth.

Its roles:

- baseline feature layer;
- merge-ready PPO text panel;
- teacher seed for LLM comparison;
- failure-discovery tool.

Feature families:

- earnings/guidance;
- company risk;
- rates;
- inflation;
- credit;
- labor/growth;
- market volatility;
- energy;
- housing;
- legal/regulatory;
- supply chain;
- consumer demand;
- margin pressure;
- capital return;
- M&A;
- risk/uncertainty/sentiment/action relevance proxies.

## Macro Rule Engine

Official macro observations now use a dedicated macro-rule engine. Generic LLM
or generic keyword sentiment should not decide macro impact direction.

Observed effect:

| Metric | Before macro rules | After macro rules |
|---|---:|---:|
| Official macro non-neutral share | 1.000 | 0.162 |
| Average risk intensity | 0.469843 | 0.309855 |
| Extraction-readiness proxy | 0.575297 | 0.378257 |

Interpretation: macro rows should often be neutral context unless
series-specific thresholds indicate stress or support.

## Mistral Vs Codex Source-Quality Run

Recent source-quality Mistral run:

| Metric | Value |
|---|---:|
| Prediction rows | 122 |
| Successful rows | 122 |
| Failed rows | 0 |
| Impact direction accuracy vs Codex-rule | 0.245902 |
| Signal precision vs Codex-rule | 0.678279 |
| Signal recall vs Codex-rule | 0.326708 |
| Signal F1 vs Codex-rule | 0.418499 |

Interpretation:

- Mistral is high-precision/low-recall relative to Codex-rule;
- it often under-tags rather than over-tags;
- earnings/guidance and legal/regulatory are more stable than generic macro
  direction;
- Mistral must be prompt-improved and evaluated against human labels before
  being scaled.

## Event-Study Feedback

Implemented script:

```text
features/build_event_study_feedback.py
```

Event-study feedback uses future returns and is diagnostic-only.

### Mistral vs Codex, `decision_date`

Rows: 122.

| Horizon | Codex direction accuracy | Mistral direction accuracy |
|---|---:|---:|
| Event day | 0.3279 | 0.3361 |
| +1d | 0.2951 | 0.4508 |
| +3d | 0.4672 | 0.2787 |
| +10d | 0.4754 | 0.2705 |
| +21d | 0.5000 | 0.3033 |

### Mistral vs Codex, `available_at_first_trading_day`

Rows: 122.

| Horizon | Codex direction accuracy | Mistral direction accuracy |
|---|---:|---:|
| Event day | 0.3197 | 0.3934 |
| +1d | 0.4180 | 0.4098 |
| +3d | 0.4590 | 0.2377 |
| +10d | 0.5738 | 0.2377 |
| +21d | 0.4918 | 0.2787 |

Main finding:

```text
Codex-rule extraction currently aligns better with +3d/+10d/+21d realized
reaction. Mistral is sometimes more competitive on event-day/+1d reaction.
Neither is final ground truth.
```

## Human Adjudication State

Balanced top-50 disagreement sample exists.

Current user feedback:

- rows 1-5: Codex preferred;
- row 6: pending original document review;
- rows 7-25: Codex preferred.

This is not yet a final human gold set because numeric labels and explicit
correct tags still need adjudication.

## Known Caveats

- Event-study feedback currently uses equal-weight Dow abnormal return; sector
  and SPY/DIA benchmarks are still needed.
- Exact intraday event timing is not fully modeled.
- Duplicate-event collapse is not yet implemented.
- Source-family slots are needed so company IR and SEC exhibits do not vanish
  behind generic SEC sections.
- Current Mistral results are against Codex-rule teacher labels, not human
  truth.
- Event-study labels must never enter PPO panels.

