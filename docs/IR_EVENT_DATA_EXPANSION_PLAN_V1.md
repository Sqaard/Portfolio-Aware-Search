# IR Event Data Expansion Plan V1

Purpose: expand FinPortfolio_IR from document retrieval into point-in-time event evidence that can feed FinGPT fixed features and only later become a CHRL candidate after cheap firewall tests.

Right governed: READ -> RETRIEVE -> SUMMARIZE -> FEATURE. This plan does not grant the right to ACT or the right to CAPITAL.

Core principle: the target is not more data. The target is earlier, cleaner, point-in-time, economically causal events that survive controls before PPO sees them.

## 1. Executive Summary

Current text extraction is finally company-specific, but not a stable stock-alpha. The next IR expansion should stop chasing generic text and build an event ledger around financially causal moments:

1. Earnings release/call events with exact before-open/after-close timing and actual-vs-consensus surprises.
2. Analyst estimate revisions and rating/target revisions with revision breadth/dispersion.
3. Guidance/promise-state events extracted from official releases/transcripts and later checked against delivery.

Build order:

1. Start with an earnings-event spine: company IR releases + SEC 8-K exhibits + one estimates provider.
2. Add analyst revision history only if the source has true historical point-in-time snapshots.
3. Add guidance/promise ledger from official language using schema-bound GPT-5.5 extraction after retrieval.

No feature enters PPO state until it passes PIT, coverage, cross-sectional IC, macro/sector/source controls, temporal nulls, and a capacity-fair PPO twin.

## 2. Source Inventory

Legend: PIT = point-in-time reconstructability; TS = timestamp precision; Hist = history depth; Cov = coverage; Bias = survivorship/source bias risk; Lic = licensing risk; Cost = expected cost; Access = implementation difficulty; CHRL = expected usefulness.

| Event family | Source | Type | PIT / TS / Hist | Cov | Bias / Lic / Cost / Access | Expected CHRL use |
| --- | --- | --- | --- | --- | --- | --- |
| Earnings releases, 8-K exhibits, guidance | [SEC EDGAR submissions/companyfacts APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Free SEC | PIT strong; timestamp filing acceptance; deep history | US public firms | Low lic; free; easy-medium | Strong for official corroboration, weak for exact event time if filing lags PR |
| Earnings releases, product/strategy PRs | Company IR sites, RSS, sitemaps, GlobeNewswire/BusinessWire links from IR | Free company IR | PIT depends on crawler first_seen; PR timestamp often exact | Issuer-specific | Low-medium lic; crawler fragile | Top source for event_time, guidance text, promise language |
| Earnings calendars, surprises, transcripts | [Financial Modeling Prep API](https://site.financialmodelingprep.com/developer/docs) | Paid/low-cost API | PIT good if endpoint has historical dates; TS varies | Broad US/global | Medium lic; paid; easy | Fast MVP for EPS/revenue surprise and transcript seed data |
| Earnings surprises, recommendations, price target, transcripts/news | [Finnhub API docs](https://finnhub.io/docs/api) | Paid/free tiers | PIT varies by endpoint; TS good for news; estimates need audit | Broad equities | Medium lic; easy | Good prototype source for surprise + analyst/recommendation signals |
| Earnings calendar, earnings, income statement | [Alpha Vantage API docs](https://www.alphavantage.co/documentation/) | Free/paid API | PIT weak unless timestamped snapshots are stored by us | Broad | Low-medium lic; easy; rate limits | Useful fallback, not enough alone for revisions |
| Estimates, surprises, revisions, institutional datasets | [Intrinio / Zacks datasets](https://docs.intrinio.com/documentation/web_api) | Professional API | PIT strong if using historical Zacks/estimate timestamps | Broad US | Paid; licensing important; medium | Best candidate for analyst revision history |
| Zacks estimates via data product | [Nasdaq Data Link](https://docs.data.nasdaq.com/) / Zacks packages | Paid dataset/API | PIT depends on package fields and vintage | Broad | Paid; licensing; medium | Strong for estimates if historical vintages available |
| Analyst ratings, PT changes, guidance/news | [Benzinga APIs](https://docs.benzinga.com/) | Paid event/news API | TS good; PIT good if archived by vendor | US-focused | Paid; medium lic; easy-medium | Strong for rating/target events and corporate news |
| News/event feed, corporate actions | [Polygon.io APIs](https://polygon.io/docs) | Paid API | TS good for news/corporate actions | US market | Paid; medium lic; easy | Useful for event timestamping, less for consensus revisions |
| Corporate actions, dividends, splits, M&A | QUODD GlobalCorporateActions, Polygon/Massive, exchange/vendor corporate-action feeds | Paid API/feed | TS varies; store first_seen | US/global depending vendor | Paid; licensing and vendor continuity risk | Useful for dividend/buyback/M&A/corporate-action layer |
| Professional estimates/events/transcripts | FactSet, LSEG/Refinitiv, Bloomberg, S&P Capital IQ | Enterprise | PIT strongest if vintage/event-time fields exist | Best institutional coverage | High cost/licensing; hard access | Gold standard, but only if budget/permissions exist |
| Earnings call transcripts | Quartr, FactSet, AlphaSense, BamSEC, Seeking Alpha transcript pages | Paid/mixed | TS varies; PIT must use publish/first_seen | Broad but licensing-sensitive | Medium-high lic; scraping risk | Strong for management tone, guidance, promise specificity |
| Credit downgrades, debt, covenant stress | S&P/Moody's/Fitch feeds, SEC 8-K, prospectuses, TRACE/FINRA, company PRs | Mixed | Rating timestamps strong if paid; SEC may lag | Issuer-dependent | Paid for ratings; medium-hard | Strong root-risk/cash gate candidate |
| Litigation/regulatory investigations | DOJ/FTC/SEC press releases, court dockets, company 8-K, regulator sites | Free/paid | TS strong for official releases; dockets hard | Sparse | Low lic for official; hard normalization | Useful for tail-risk events, sparse for PPO stock-alpha |
| Macro shock context | FRED/Fed/Treasury/BLS/BEA official APIs | Free official | PIT good if release timestamps captured | Macro only | Low lic; easy | Control variables, not primary stock-alpha |

Recommended source posture:

- Free MVP: SEC EDGAR + company IR crawler + FMP/Finnhub/Alpha Vantage for calendar/surprise prototypes.
- Research-grade path: add Intrinio/Zacks or Nasdaq Data Link Zacks for historical analyst estimates/revisions.
- Enterprise path: FactSet/LSEG/Bloomberg if the project needs professional timestamped estimates, transcripts, credit, and events.

## 3. PIT Event Schema

Store events as append-only ledger rows. A changed event creates a new version; no silent overwrite.

```json
{
  "event_id": "sha256(source_id|ticker|event_type|event_time_utc|document_hash|version)",
  "source_id": "company_ir_aapl",
  "source_family": "company_ir|sec_edgar|estimate_api|transcript|news_api|regulator|credit_rating",
  "ticker": "AAPL",
  "company_id": "0000320193",
  "cik": "0000320193",
  "event_type": "earnings|analyst_revision|guidance|corporate_action|risk_stress",
  "event_subtype": "eps_surprise|revenue_surprise|guidance_lowered|rating_downgrade|buyback|liquidity_warning",
  "event_time_utc": "2023-02-02T21:30:00Z",
  "available_at_utc": "2023-02-02T21:33:00Z",
  "retrieval_cutoff_utc": "2023-02-03T14:25:00Z",
  "decision_time_utc": "2023-02-03T14:30:00Z",
  "market_session_tag": "before_open|during_market|after_close|weekend",
  "before_open": false,
  "during_market": false,
  "after_close": true,
  "document_hash": "sha256:...",
  "raw_source_url": "https://investor.apple.com/...",
  "canonical_url": "https://...",
  "source_document_id": "company_aapl_...",
  "extracted_features": {"earnings_surprise_direction": 1.0},
  "source_reliability_score": 0.0,
  "timestamp_confidence": 0.0,
  "point_in_time_valid_flag": false,
  "pit_failure_reason": "",
  "version": 1,
  "created_at_utc": "crawler clock"
}
```

Hard PIT rules:

- `available_at_utc <= retrieval_cutoff_utc <= decision_time_utc`.
- `event_time_utc <= available_at_utc`; if not, quarantine.
- After-close events cannot affect same-day close decision; they can affect next eligible decision.
- SEC filing date is corroboration, not automatically the economic event time.
- Live fetch can enrich today, but backtests use only ledger rows whose `available_at_utc` existed at the decision time.

## 4. Fixed Feature Schema

All features are bounded, fixed-column, and source-backed. LLMs may extract values only after retrieval and must return citations/confidence. Diagnostics remain research-only until promoted.

| Feature | Range | Applies to | Meaning |
| --- | ---: | --- | --- |
| `earnings_surprise_direction` | [-1, 1] | earnings | Beat/miss direction across EPS/revenue/margin composite |
| `earnings_surprise_magnitude` | [0, 1] | earnings | Winsorized absolute surprise vs consensus |
| `eps_surprise_z` | [-3, 3] | earnings | EPS surprise standardized by historical surprise distribution |
| `revenue_surprise_z` | [-3, 3] | earnings | Revenue surprise standardized |
| `margin_surprise_z` | [-3, 3] | earnings | Gross/operating margin surprise standardized |
| `guidance_revision_direction` | [-1, 1] | guidance | Raised/lowered/withdrawn guidance |
| `guidance_revision_magnitude` | [0, 1] | guidance | Size of revision relative to consensus or prior company guide |
| `guidance_specificity` | [0, 1] | guidance | Numeric, dated, segment-specific guidance density |
| `management_confidence_delta` | [-1, 1] | guidance/call | Change from prior call/release, same company |
| `promise_pressure` | [0, 1] | promise | Intensity of forward commitments and implied delivery burden |
| `promise_delivery_risk` | [0, 1] | promise | Risk that prior promises are not being delivered |
| `promise_miss_count_rolling_4q` | [0, 4] | promise | Count of missed promises in last four quarters |
| `analyst_eps_revision_direction` | [-1, 1] | analyst | Net EPS estimate revision direction |
| `analyst_revision_breadth` | [0, 1] | analyst | Share of analysts revising up/down |
| `consensus_dispersion_delta` | [-1, 1] | analyst | Change in disagreement/uncertainty |
| `rating_change_direction` | [-1, 1] | analyst | Upgrade/downgrade direction |
| `price_target_revision_magnitude` | [-1, 1] | analyst | PT change relative to price |
| `buyback_intensity` | [0, 1] | corporate | Announced buyback size vs market cap |
| `dividend_change_direction` | [-1, 1] | corporate | Increase/cut/suspension |
| `debt_refinancing_stress` | [0, 1] | corporate/risk | Refinancing/maturity/covenant stress |
| `liquidity_stress` | [0, 1] | risk | Cash, going-concern, covenant, maturity pressure |
| `regulatory_pressure` | [0, 1] | risk | Investigation/litigation/antitrust pressure |
| `supply_chain_shock_intensity` | [0, 1] | risk | Supplier/production/delivery disruption |
| `evidence_specificity` | [0, 1] | all | Concrete numbers, dates, named segments |
| `numeric_evidence_density` | [0, 1] | all | Numeric claims per token/span |
| `uncertainty_intensity` | [0, 1] | all | Explicit uncertainty, withdrawal, caution |
| `downside_risk_intensity` | [0, 1] | all | Downside/risk language tied to mechanism |
| `actionability_decay_days` | [0, 63] | all | Days since event, capped by event family half-life |
| `source_quality_score` | [0, 1] | all | Source card score at event time |
| `timestamp_confidence` | [0, 1] | all | Confidence in event/availability timestamp |

Recommended aggregation to daily CHRL panel:

- Keep raw event table separate.
- Daily features per ticker are decayed summaries: latest event, rolling 5/21/63 day signed intensity, event counts, and confidence-weighted averages.
- Do not aggregate across tickers before cross-sectional tests; macro-like collapse was a known failure mode.

## 5. Firewall Validation Plan Before PPO

### 5.1 Coverage tests

- Events per ticker/year by event family.
- Source coverage by year and sector.
- Missingness by sector/market-cap bucket.
- Train/test drift in coverage and source mix.
- Independent event episodes, not only daily rows.
- Red flag: one source/year dominates positive results.

Minimum to continue:

- Earnings spine: near-complete 4 quarterly events per ticker/year for the target universe.
- Analyst revisions: enough distinct revision episodes per year; otherwise do not train CHRL.
- Risk events: sparse is acceptable only for root-risk/drawdown tests, not stock-alpha claims.

### 5.2 PIT tests

- No row with `available_at_utc > decision_time_utc`.
- No row with `retrieval_cutoff_utc > decision_time_utc`.
- After-close events affect next eligible decision, not same-day close.
- Event-time source priority: official PR timestamp > vendor event timestamp > SEC acceptance time > crawler first_seen; store confidence.
- Future-lead leakage test: intentionally shift features forward; if performance improves suspiciously, quarantine.

Hard acceptance: zero PIT violations in the candidate panel.

### 5.3 Cross-sectional alpha tests

- Forward 1d/5d/21d/63d excess returns.
- Sector-relative and market-neutral returns.
- Cross-sectional IC and rank IC by year/fold.
- Phase-free overlapping-horizon tests; do not cherry-pick offset 0.
- Purged CV with embargo for overlapping labels.
- HAC/Newey-West or block/circular nulls, not iid-only tests.

Pass shape:

- Sign-stable IC across years/folds.
- Effect not driven by one lucky year or one mega-cap.
- Enough independent episodes after de-overlap.

### 5.4 Risk/root tests

- Drawdown onset prediction.
- Drawdown deterioration and recovery onset.
- Risk-off action usefulness: cash/risk allocation, not stock selection.
- Compare against full macro panel and volatility/drawdown overlay.

Pass shape:

- Improves risk timing after macro controls.
- Does not simply re-encode VIX/rates/market trend.

### 5.5 Controls

Required controls before CHRL:

- Macro-only control.
- Sector-only control.
- Source-coverage control.
- Stale-lag control.
- Future-lead leakage control.
- Random timestamp shuffle.
- Ticker permutation within date.
- Source-family permutation.
- Obs-dim matched noise placebo.
- Capacity-fair PPO twin only after cheap tests pass.

## 6. Acceptance Criteria for CHRL Candidate Status

A signal family becomes a CHRL candidate only if all are true:

1. Company-specific cross-sectional variance passes.
2. IC sign is stable across years/folds and not phase-selected.
3. Effect survives macro, sector, source-coverage, and stale-lag controls.
4. Effect survives temporal nulls and ticker/date permutations.
5. Feature beats obs-dim matched noise placebo in cheap tests.
6. Enough independent event episodes remain after purging/embargo.
7. Economic mechanism is clear: cash-flow, balance-sheet, policy, plumbing, governance, or delivery promise.
8. Result is not one lucky year, one source, or one sector.
9. Only then build a CHRL ablation package with capacity-fair PPO twin.
10. Frozen OOS is used once for confirmation, not tuning.

## 7. Ranked Implementation Roadmap

### Top 3 to implement first

| Rank | Path | Why first | Build first |
| ---: | --- | --- | --- |
| 1 | Earnings event spine | Dense, recurring, economically causal, enough episodes | Company IR + SEC 8-K + earnings calendar/surprise API; exact timing; before/after market tag |
| 2 | Analyst estimate revision ledger | Direct repricing channel; likely earlier than filings | Historical EPS/revenue revisions, breadth, dispersion, rating/PT changes from Intrinio/Zacks/Nasdaq/Finnhub/Benzinga |
| 3 | Guidance/promise-state ledger | Connects management promises to later delivery; more causal than raw tone | Official PR + transcript retrieval; GPT-5.5 schema extraction; promise-vs-delivery joins |

### Medium priority

| Rank | Path | Why | Caveat |
| ---: | --- | --- | --- |
| 4 | Debt/refinancing/liquidity events | Strong root-risk/cash gate candidate | Sparse; paid credit data may be needed |
| 5 | Buyback/dividend/capital return events | Clear capital allocation mechanism | Often already priced; need size vs market cap |
| 6 | Regulatory/litigation events | Tail-risk episodes | Sparse and sector-concentrated |
| 7 | Restructuring/layoff/CEO-CFO events | Governance/turnaround signal | Ambiguous sign; must separate distress vs efficiency |
| 8 | Supply-chain/commodity exposure events | Mechanism-rich for industrials/energy | Needs sector mapping and external shock data |

### Low priority or dangerous

| Rank | Path | Why dangerous |
| ---: | --- | --- |
| 9 | Generic news sentiment | Usually late, noisy, licensing-sensitive, macro-like |
| 10 | Product launches without quantified financial effect | Attractive narrative, weak causal link |
| 11 | Social/media hype | High noise, manipulation risk, PIT reconstruction hard |
| 12 | SEC-only earnings proxy | Filing date often too late; already failed as PEAD proxy directionally |
| 13 | LLM-direct "important news" search | Violates retrieval-first and citation/PIT discipline |

## 8. Most Likely Failure Modes

| Path | Failure mode | Mitigation |
| --- | --- | --- |
| Earnings spine | Event already priced by after-close open; bad consensus data | Use exact timing; test next eligible decision; compare multiple consensus sources |
| Analyst revisions | Paid data lacks true vintage; revision timestamps are reconstructed | Demand vendor timestamp fields; store first_seen; reject non-vintage history |
| Guidance/promise | LLM overreads vague language | Require numeric/cited facts, confidence, and missing-info flags |
| Corporate actions | Sign ambiguity: buybacks can mean confidence or no growth | Encode mechanism and size, not binary event |
| Debt/liquidity | Sparse episodes, survivorship bias | Include delisted/distressed names if broad universe; root-risk tests only |
| Regulatory/litigation | Slow legal process, market may know earlier | Use official timestamp plus news timestamp; test decay windows |
| Transcripts | Publication lag and licensing | Store transcript available_at; do not use call time if transcript unavailable |
| Company IR | Selective optimism and omission | Pair with SEC, estimates, and later delivery checks |
| News APIs | Duplicate syndication creates fake confidence | Hash/dedup and diversity penalty |
| All event data | Source coverage drives alpha | Source-coverage controls and ticker/date permutations |

## 9. Clear Recommendation

Build the earnings-event spine first.

Concrete first sprint:

1. Add `event_sources_v1` source cards for SEC EDGAR, company IR, FMP/Finnhub/Alpha Vantage prototype APIs, and one preferred professional estimate source if available.
2. Create `event_ledger_v1` table with the PIT schema above.
3. Implement earnings event ingestion for the Dow-30/Dow-29 universe 2010-2023:
   - earnings PR timestamp;
   - SEC 8-K exhibit corroboration;
   - EPS/revenue actual and consensus;
   - before-open/after-close tag;
   - source reliability and timestamp confidence.
4. Build daily event features with 1/5/21/63 day decay.
5. Run coverage, PIT, IC, controls, and temporal nulls before any CHRL package.
6. Only if earnings spine passes, add analyst revisions and guidance/promise ledger.

This is the highest-ROI path because it directly targets the known bottleneck: not model control, but earlier, causal, PIT-safe information.

## 10. Source Links Used For Source Inventory

- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- Financial Modeling Prep API docs: https://site.financialmodelingprep.com/developer/docs
- Finnhub API docs: https://finnhub.io/docs/api
- Alpha Vantage API docs: https://www.alphavantage.co/documentation/
- Intrinio API docs: https://docs.intrinio.com/documentation/web_api
- Nasdaq Data Link docs: https://docs.data.nasdaq.com/
- Benzinga API docs: https://docs.benzinga.com/
- Polygon.io docs: https://polygon.io/docs
- FactSet Events and Transcripts API: https://developer.factset.com/api-catalog/events-and-transcripts-api
- Quartr API docs: https://quartr.com/docs/api-reference
- QUODD corporate actions API overview: https://www.quodd.com/insights/introducing-new-corporate-actions-cloud-api
