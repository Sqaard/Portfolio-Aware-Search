# Pre-PPO Diagnostics Findings

- valid train rows: `85753`
- valid OOS rows: `9686`
- text features audited: `64`
- lean feature count: `20` total = 12 base + 8 text
- teacher action diagnostics: `not_run_no_teacher_action_or_position_file_found`

## Conclusion

The text features show weak incremental signal in simple OOS models. Use the lean feature set as the first PPO sanity ablation; do not start with the 56-feature core state unless the lean run is stable.

## Lean Text Features

- `portfolio_signal_credit_count`
- `portfolio_text_avg_sentiment_proxy`
- `stock_signal_earnings_guidance_count`
- `stock_text_avg_sentiment_proxy`
- `stock_signal_margin_pressure_count`
- `stock_signal_company_risk_count`
- `stock_signal_labor_growth_count`
- `stock_signal_energy_count`

## Classification ROC AUC

| variant | fwd_20d_drawdown_flag_auc | fwd_20d_drawdown_flag_delta_auc | fwd_20d_high_vol_flag_auc | fwd_20d_high_vol_flag_delta_auc | fwd_20d_risk_flag_auc | fwd_20d_risk_flag_delta_auc |
| --- | --- | --- | --- | --- | --- | --- |
| base_macro | 0.6676 | 0.0000 | 0.7981 | 0.0000 | 0.7545 | 0.0000 |
| base_macro_plus_portfolio_text_core | 0.6683 | 0.0007 | 0.7983 | 0.0002 | 0.7552 | 0.0007 |
| base_macro_plus_stock_text_core | 0.6683 | 0.0007 | 0.7986 | 0.0006 | 0.7556 | 0.0011 |
| base_macro_plus_all_text_core | 0.6686 | 0.0011 | 0.7988 | 0.0007 | 0.7565 | 0.0019 |
| base_macro_plus_all_text_all | 0.6677 | 0.0002 | 0.7986 | 0.0005 | 0.7559 | 0.0014 |
| base_macro_plus_text_lean_v1 | 0.6678 | 0.0002 | 0.7986 | 0.0006 | 0.7558 | 0.0013 |

## Regression Spearman

| variant | fwd_20d_realized_vol_spearman | fwd_20d_realized_vol_delta_spearman | fwd_20d_max_drawdown_spearman | fwd_20d_max_drawdown_delta_spearman | fwd_5d_return_spearman | fwd_5d_return_delta_spearman |
| --- | --- | --- | --- | --- | --- | --- |
| base_macro | 0.6061 | 0.0000 | 0.3277 | 0.0000 | 0.0565 | 0.0000 |
| base_macro_plus_stock_text_core | 0.6069 | 0.0008 | 0.3284 | 0.0007 | 0.0548 | -0.0017 |
| base_macro_plus_all_text_core | 0.6051 | -0.0010 | 0.3285 | 0.0008 | 0.0509 | -0.0056 |
| base_macro_plus_all_text_all | 0.6046 | -0.0015 | 0.3267 | -0.0010 | 0.0503 | -0.0062 |
| base_macro_plus_text_lean_v1 | 0.6072 | 0.0011 | 0.3287 | 0.0011 | 0.0558 | -0.0007 |
