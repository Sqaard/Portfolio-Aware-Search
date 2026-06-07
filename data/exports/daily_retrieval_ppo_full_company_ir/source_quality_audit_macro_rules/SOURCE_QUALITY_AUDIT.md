# Source Quality Audit

This report separates source integrity from extraction quality. Rule-based feature metrics are proxy diagnostics, not ground truth.

## Source Families

| source_family | contexts | unique_docs | integrity | extraction_proxy | provenance | causal |
|---|---:|---:|---:|---:|---:|---:|
| company_ir | 747 | 101 | 1.000 | 0.816 | 1.000 | 1.000 |
| sec_edgar | 11196 | 854 | 1.000 | 0.438 | 1.000 | 1.000 |
| official_macro | 16550 | 2501 | 0.924 | 0.378 | 0.882 | 1.000 |

## Source Types

| source_type | contexts | unique_docs | integrity | extraction_proxy | avg signals | non-neutral |
|---|---:|---:|---:|---:|---:|---:|
| sec_filing_exhibit | 3029 | 166 | 1.000 | 0.514 | 2.42 | 0.885 |
| sec_filing_section | 8167 | 688 | 1.000 | 0.410 | 1.98 | 0.528 |
| company_earnings_release | 471 | 54 | 0.954 | 0.812 | 5.06 | 0.998 |
| company_press_release | 136 | 25 | 0.925 | 0.699 | 4.12 | 0.993 |
| official_macro_release | 16550 | 2501 | 0.924 | 0.378 | 2.01 | 0.162 |
| company_official_archive | 73 | 15 | 0.915 | 0.861 | 6.64 | 1.000 |
| company_financial_report | 51 | 5 | 0.905 | 0.872 | 4.59 | 1.000 |
| company_sec_filing_hub | 16 | 2 | 0.902 | 0.900 | 7.81 | 1.000 |

## Existing Mistral-vs-Codex Teacher Evidence

No Mistral comparison rows were provided.

## Next LLM Evaluation

Prepared `122` source-stratified rows for LLM/human evaluation.
Use this seed to compare Mistral/Codex/FinBERT/lexicon by source_type before promoting any source family into PPO features.
