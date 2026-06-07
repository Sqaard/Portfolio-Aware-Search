from pathlib import Path
import http.client
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finportfolio_ir.my_vibe import build_portfolio_impact_prompt
from finportfolio_ir.portfolio_summary import summarize_portfolio
from web_app import FinPortfolioWebService, _llm_config_for_task, build_server, resolve_llm_config, validate_favorite_website


class WebAppServiceTests(unittest.TestCase):
    def _service(self, tmpdir: str) -> FinPortfolioWebService:
        return FinPortfolioWebService(
            settings_path=Path(tmpdir) / "settings.json",
            documents_path=ROOT / "data" / "processed_documents" / "documents.jsonl",
        )

    def _result(
        self,
        service: FinPortfolioWebService,
        doc_id: str,
        title: str,
        available_at: str,
        score: float,
    ) -> dict:
        return service._result_row(
            {
                "doc_id": doc_id,
                "title": title,
                "text": title,
                "canonical_url": f"https://sec.gov/{doc_id}",
                "source": "SEC EDGAR",
                "source_type": "sec_filing_section",
                "published_at": available_at,
                "available_at": available_at,
                "matched_tickers": ["JPM"],
                "matched_holdings": ["JPM"],
                "event_tags": ["company_risk"],
                "risk_terms": ["risk"],
                "source_credibility": 1.0,
            },
            score,
            {
                "calibrated_signal_score": score,
                "risk_alert_score": min(4.0, score),
                "upside_signal_score": 0.0,
                "active_signals": ["signal_company_risk"],
            },
        )

    def test_dashboard_payload_has_expected_ui_blocks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            payload = service.dashboard_payload()

        self.assertEqual(payload["language"], "en")
        self.assertEqual(len(payload["macro_dashboard"]["what_matters_cards"]), 3)
        self.assertEqual(len(payload["macro_portfolio_translation"]["cards"]), 3)
        self.assertIn("portfolio_summary", payload)
        self.assertIn("icons", payload)
        self.assertIn("llm", payload)
        self.assertIn("llm_default_model", payload["llm"])
        self.assertIn("llm_providers", payload["llm"])
        self.assertIn("openai", {row["id"] for row in payload["llm"]["llm_providers"]})
        self.assertIn("deepseek", {row["id"] for row in payload["llm"]["llm_providers"]})
        self.assertIn("paratera_deepseek", {row["id"] for row in payload["llm"]["llm_providers"]})
        self.assertEqual(len(payload["allowed_tickers"]), 30)
        self.assertIn("AAPL", {row["ticker"] for row in payload["allowed_tickers"]})
        self.assertIn("chart_lab", payload)
        self.assertEqual(payload["chart_lab"]["default_mode"], "structured")
        self.assertEqual({row["id"] for row in payload["chart_lab"]["modes"]}, {"structured"})
        self.assertEqual(len(payload["chart_lab"]["charts"]), 3)
        self.assertNotIn("macro", {row["scope"] for row in payload["chart_lab"]["charts"]})
        self.assertNotIn("company_filing_risk", {row["id"] for row in payload["chart_lab"]["charts"]})

    def test_document_view_html_renders_full_text_without_download(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            with (
                patch("web_app.server_llm_secret", return_value=""),
                patch.object(
                    service,
                    "_records_by_doc_ids",
                    return_value={
                        "doc_1": {
                            "doc_id": "doc_1",
                            "title": "Apple Inc. 10-Q filing filed 2023-02-03",
                            "body": "Item 1A. Risk Factors Full filing text.",
                            "canonical_url": "https://www.sec.gov/ixviewer/doc_1",
                            "source_type": "sec_filing_section",
                            "available_at": "2023-02-03T16:00:00Z",
                            "matched_tickers": ["AAPL"],
                            "event_tags": ["company_risk"],
                        }
                    },
                ),
            ):
                html_text = service.document_view_html("doc_1")

        self.assertIn("Apple Inc. 10-Q filing", html_text)
        self.assertIn("Document Brief", html_text)
        self.assertIn("Original cached text", html_text)
        self.assertIn("Item 1A. Risk Factors Full filing text.", html_text)
        self.assertIn("Open original source", html_text)
        self.assertIn('id="cosmicBackdrop"', html_text)
        self.assertIn("--bg: #08111f", html_text)
        self.assertNotIn("download", html_text.lower())

    def test_chart_lab_payload_uses_panel_features(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("web_app.server_llm_secret", return_value=""):
            panel_path = Path(tmpdir) / "panel.csv"
            panel_path.write_text(
                "\n".join(
                    [
                        "date,tic,rev_q,EPS,stock_text_avg_risk_intensity,portfolio_text_doc_count,rates_lsc_policy_pressure_score",
                        "2020-01-01,AAPL,100,1.0,0.2,5,0.4",
                        "2020-04-01,AAPL,120,1.2,0.4,6,0.6",
                        "2020-01-01,JPM,80,0.9,0.3,5,0.4",
                    ]
                ),
                encoding="utf-8",
            )
            service = FinPortfolioWebService(
                settings_path=Path(tmpdir) / "settings.json",
                documents_path=ROOT / "data" / "processed_documents" / "documents.jsonl",
                chart_panel_path=panel_path,
            )
            company = service.chart_lab_payload({"ticker": ["AAPL"], "chart_id": ["company_revenue_eps"], "mode": ["structured"]})
            macro = service.chart_lab_payload({"ticker": ["AAPL"], "chart_id": ["macro_rates_pressure"], "mode": ["structured"]})

        self.assertTrue(company["available"])
        self.assertEqual(company["ticker"], "AAPL")
        self.assertIn("rev_q", {row["key"] for row in company["series"]})
        self.assertTrue(macro["available"])
        self.assertIn("rates_lsc_policy_pressure_score", {row["key"] for row in macro["series"]})

    def test_chart_lab_keeps_latest_carried_forward_date(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("web_app.server_llm_secret", return_value=""):
            panel_path = Path(tmpdir) / "panel.csv"
            rows = ["date,tic,rev_q,EPS"]
            for index in range(9):
                rows.append(f"2020-{index + 1:02d}-01,AAPL,{100 + index},1.0")
            rows.append("2021-01-01,AAPL,108,1.0")
            panel_path.write_text("\n".join(rows), encoding="utf-8")
            service = FinPortfolioWebService(
                settings_path=Path(tmpdir) / "settings.json",
                documents_path=ROOT / "data" / "processed_documents" / "documents.jsonl",
                chart_panel_path=panel_path,
            )
            payload = service.chart_lab_payload({"ticker": ["AAPL"], "chart_id": ["company_revenue_eps"], "mode": ["structured"]})

        revenue = next(row for row in payload["series"] if row["key"] == "rev_q")
        self.assertEqual(revenue["latest"]["date"], "2021-01-01")
        self.assertEqual(revenue["latest"]["value"], 108.0)

    def test_chart_lab_analysis_returns_rule_based_trends(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("web_app.server_llm_secret", return_value=""):
            panel_path = Path(tmpdir) / "panel.csv"
            panel_path.write_text(
                "\n".join(
                    [
                        "date,tic,rev_q,EPS,stock_signal_earnings_guidance_count",
                        "2020-01-01,AAPL,100,1.0,0",
                        "2020-04-01,AAPL,120,1.2,1",
                    ]
                ),
                encoding="utf-8",
            )
            service = FinPortfolioWebService(
                settings_path=Path(tmpdir) / "settings.json",
                documents_path=ROOT / "data" / "processed_documents" / "documents.jsonl",
                chart_panel_path=panel_path,
            )
            analysis = service.analyze_chart_lab_payload(
                {"ticker": "AAPL", "chart_id": "company_revenue_eps", "mode": "structured", "llm": {}}
            )

        self.assertEqual(analysis["analysis_mode"], "rule_based")
        self.assertEqual(analysis["chart_id"], "company_revenue_eps")
        self.assertGreaterEqual(len(analysis["trends"]), 2)
        self.assertIn("Revenue", {row["label"] for row in analysis["trends"]})
        self.assertIn("headline", analysis)
        self.assertIn("commentary", analysis)
        self.assertTrue(analysis["takeaways"])
        self.assertFalse(analysis["api_key_received"])
        self.assertFalse(analysis["api_key_persisted"])

    def test_portfolio_ticker_analysis_uses_chart_lab_and_documents(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("web_app.server_llm_secret", return_value=""):
            panel_path = Path(tmpdir) / "panel.csv"
            panel_path.write_text(
                "\n".join(
                    [
                        "date,tic,rev_q,EPS,OPM,NPM,debt_ratio,cur_ratio,stock_signal_earnings_guidance_count,stock_signal_margin_pressure_count,stock_signal_credit_count,stock_text_max_event_severity,stock_signal_company_risk_count,stock_signal_legal_regulatory_count",
                        "2020-01-01,AAPL,100,1.0,0.20,0.10,0.40,1.5,0,0,0,0,0,0",
                        "2020-04-01,AAPL,120,1.2,0.22,0.11,0.35,1.6,1,0,0,0.1,1,0",
                    ]
                ),
                encoding="utf-8",
            )
            service = FinPortfolioWebService(
                settings_path=Path(tmpdir) / "settings.json",
                documents_path=ROOT / "data" / "processed_documents" / "documents.jsonl",
                chart_panel_path=panel_path,
            )
            service.save_settings({"portfolio": [{"ticker": "AAPL", "purchase_price": "100", "quantity": "2"}], "favorite_websites": []})
            analysis = service.analyze_portfolio_ticker({"ticker": "AAPL", "llm": {}})

        self.assertEqual(analysis["ticker"], "AAPL")
        self.assertFalse(analysis["llm_used"])
        self.assertGreaterEqual(len(analysis["charts"]), 3)
        self.assertTrue(all(chart.get("analysis") for chart in analysis["charts"]))
        self.assertIn("analysis_markdown", analysis)
        self.assertNotIn("secret", json.dumps(analysis).lower())

    def test_portfolio_ticker_remote_analysis_only_calls_llm_for_first_chart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            panel_path = Path(tmpdir) / "panel.csv"
            panel_path.write_text(
                "\n".join(
                    [
                        "date,tic,rev_q,EPS,OPM,NPM,debt_ratio,cur_ratio",
                        "2020-01-01,AAPL,100,1.0,0.20,0.10,0.40,1.5",
                        "2020-04-01,AAPL,120,1.2,0.22,0.11,0.35,1.6",
                    ]
                ),
                encoding="utf-8",
            )
            service = FinPortfolioWebService(
                settings_path=Path(tmpdir) / "settings.json",
                documents_path=ROOT / "data" / "processed_documents" / "documents.jsonl",
                chart_panel_path=panel_path,
            )
            service.save_settings({"portfolio": [{"ticker": "AAPL", "purchase_price": "100", "quantity": "2"}], "favorite_websites": []})
            llm_chart = {
                "verdict": "Watch",
                "headline": "Revenue needs margin proof",
                "sentence": "Revenue rose 20.0% from 100.0 to 120.0.",
                "commentary": "Hypothesis: growth is improving. Risk: margins may not follow. Check margins next.",
                "takeaways": [],
                "points": [],
            }
            with (
                patch.object(service, "_call_llm_for_chart_analysis", return_value=llm_chart) as chart_llm,
                patch.object(service, "_call_llm_for_portfolio_ticker", return_value="## Verdict\nWatch.\n\n## Conclusion\nUse a stronger LLM for a KPI bridge."),
            ):
                analysis = service.analyze_portfolio_ticker(
                    {"ticker": "AAPL", "llm": {"api_key": "secret", "provider": "paratera_deepseek"}}
                )

        self.assertTrue(analysis["llm_used"])
        self.assertEqual(chart_llm.call_count, 1)
        self.assertEqual(analysis["charts"][0]["analysis"]["analysis_mode"], "llm")
        self.assertEqual(analysis["charts"][1]["analysis"]["analysis_mode"], "rule_based")

    def test_portfolio_markdown_normalizer_splits_sections(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            markdown = service._normalize_portfolio_markdown(
                "## What The Charts Say - Revenue improved ## Evidence Documents - Apple Inc. 10-Q filing - Risk Factors ## Conclusion Good."
            )

        self.assertIn("## What The Charts Say", markdown)
        self.assertIn("\n\n## Evidence Documents\n- Apple Inc. 10-Q filing", markdown)
        self.assertIn("\n\n## Conclusion", markdown)

    def test_settings_persist_without_llm_api_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            saved = service.save_settings(
                {
                    "portfolio": [{"ticker": "aapl", "purchase_price": "100", "quantity": "2"}],
                    "favorite_websites": ["https://www.sec.gov/path?utm_source=x"],
                    "llm_api_key": "secret",
                }
            )
            raw_text = (Path(tmpdir) / "settings.json").read_text(encoding="utf-8")

        self.assertEqual(saved["portfolio"][0]["ticker"], "AAPL")
        self.assertEqual(saved["favorite_websites"], ["https://sec.gov/"])
        self.assertNotIn("secret", raw_text)
        self.assertNotIn("llm_api_key", raw_text)

    def test_llm_provider_selects_deepseek_defaults(self):
        api_key, model, endpoint, used_server = resolve_llm_config({"provider": "deepseek", "api_key": "secret"})

        self.assertEqual(api_key, "secret")
        self.assertEqual(model, "deepseek-chat")
        self.assertEqual(endpoint, "https://api.deepseek.com/chat/completions")
        self.assertFalse(used_server)

    def test_llm_provider_selects_paratera_deepseek_defaults(self):
        api_key, model, endpoint, used_server = resolve_llm_config({"provider": "paratera_deepseek", "api_key": "secret"})

        self.assertEqual(api_key, "secret")
        self.assertEqual(model, "DeepSeek-V4-Flash")
        self.assertEqual(endpoint, "https://llmapi.paratera.com/v1/chat/completions")
        self.assertFalse(used_server)

    def test_llm_provider_selects_task_models_for_paratera_deepseek(self):
        graph_config = _llm_config_for_task({"provider": "paratera_deepseek", "api_key": "secret"}, "graph")
        post_config = _llm_config_for_task({"provider": "paratera_deepseek", "api_key": "secret"}, "post")
        portfolio_config = _llm_config_for_task({"provider": "paratera_deepseek", "api_key": "secret"}, "portfolio")

        self.assertEqual(resolve_llm_config(graph_config)[1], "DeepSeek-V4-Flash")
        self.assertEqual(resolve_llm_config(post_config)[1], "DeepSeek-V4-Flash")
        self.assertEqual(resolve_llm_config(portfolio_config)[1], "DeepSeek-V4-Flash")

    def test_llm_provider_selects_task_model_with_matching_server_key(self):
        config = _llm_config_for_task({"provider": "paratera_deepseek"}, "graph")

        with (
            patch("web_app.server_llm_secret", return_value="server-secret"),
            patch("web_app.server_llm_endpoint", return_value="https://llmapi.paratera.com/v1/chat/completions"),
            patch("web_app.server_llm_model", return_value="DeepSeek-V4-Pro"),
        ):
            api_key, model, endpoint, used_server = resolve_llm_config(config)

        self.assertEqual(api_key, "server-secret")
        self.assertEqual(model, "DeepSeek-V4-Flash")
        self.assertEqual(endpoint, "https://llmapi.paratera.com/v1/chat/completions")
        self.assertTrue(used_server)

    def test_settings_reject_non_dow30_ticker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            with self.assertRaises(ValueError):
                service.save_settings(
                    {
                        "portfolio": [{"ticker": "XXX", "purchase_price": "100", "quantity": "2"}],
                        "favorite_websites": ["https://example.com/"],
                    }
                )

    def test_favorite_url_validation_rejects_plain_text(self):
        result = validate_favorite_website("not a website")

        self.assertFalse(result["valid"])
        self.assertIn("valid URL", result["message"])

    def test_favorite_url_validation_rejects_private_hosts(self):
        result = validate_favorite_website("http://127.0.0.1:8765")

        self.assertFalse(result["valid"])
        self.assertIn("public websites", result["message"])

    def test_favorite_url_validation_accepts_reachable_site(self):
        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        with patch("web_app.urllib.request.urlopen", return_value=FakeResponse()):
            result = validate_favorite_website("https://www.sec.gov/some/path")

        self.assertTrue(result["valid"])
        self.assertEqual(result["storage_url"], "https://sec.gov/")

    def test_search_promotes_favorites_on_refresh(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("web_app.server_llm_secret", return_value=""):
            service = self._service(tmpdir)
            service.save_settings(
                {
                    "portfolio": [{"ticker": "AAPL", "purchase_price": 100, "quantity": 1}],
                    "favorite_websites": ["https://example.com/"],
                }
            )
            payload = service.search_payload("supplier concerns")

        self.assertGreater(payload["count"], 0)
        self.assertTrue(payload["results"][0]["favorite_highlight"])
        self.assertEqual(payload["results"][0]["favorite_icon"], "filled")

    def test_company_name_search_scores_like_ticker_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            record = {
                "title": "Apple Inc. 10-Q filing filed 2023-02-03",
                "body": "Apple filings and financial statements.",
                "matched_tickers": ["AAPL"],
            }

            ticker_score = service._search_score(record, "AAPL filings")
            company_score = service._search_score(record, "Apple filings")

        self.assertEqual(ticker_score, company_score)
        self.assertEqual(service._matched_entity_tickers("Home Depot risks"), ["HD"])

    def test_numeric_company_alias_maps_to_ticker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)

        self.assertEqual(service._query_entity_tickers("3M litigation risk"), ["MMM"])
        self.assertEqual(service._entity_expansion_terms("3M filings"), ["mmm", "3m"])

    def test_entity_specific_risk_demotes_wrong_company(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            features = {
                "calibrated_signal_score": 2.0,
                "risk_alert_score": 2.0,
                "event_severity_score": 1.0,
            }
            jpm_record = {
                "source_type": "sec_filing_section",
                "matched_tickers": ["JPM"],
                "event_tags": ["Company Risk"],
            }
            cat_record = {
                "source_type": "sec_filing_section",
                "matched_tickers": ["CAT"],
                "event_tags": ["Company Risk"],
            }
            macro_record = {
                "source_type": "official_macro_release",
                "matched_tickers": ["MARKET"],
                "event_tags": ["Credit"],
            }

            jpm_score = service._feature_aware_score(jpm_record, 2.0, "JPMorgan credit risk", features)
            cat_score = service._feature_aware_score(cat_record, 2.0, "JPMorgan credit risk", features)
            macro_score = service._feature_aware_score(macro_record, 2.0, "JPMorgan credit risk", features)

        self.assertGreater(jpm_score, cat_score)
        self.assertGreater(jpm_score, macro_score)

    def test_search_sort_prefers_score_before_freshness(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            rows = [
                {"doc_id": "fresh_low", "available_at": "2023-02-28T16:00:00Z", "score": 1.0},
                {"doc_id": "older_high", "available_at": "2022-02-28T16:00:00Z", "score": 20.0},
            ]

            service._sort_search_results(rows)

        self.assertEqual(rows[0]["doc_id"], "older_high")

    def test_field_reranker_prefers_exact_guidance_over_product_press(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            earnings = {
                "title": "Apple Inc. 8-K filing filed 2023-02-02 - Exhibit 99.1 Earnings Release / Investor Material",
                "source_type": "sec_filing_exhibit",
                "matched_tickers": ["AAPL"],
                "event_tags": ["Earnings Guidance"],
            }
            product = {
                "title": "Apple announces the new iPhone SE: a powerful smartphone in an iconic design - Apple",
                "source_type": "company_press_release",
                "matched_tickers": ["AAPL"],
                "event_tags": ["Company Official"],
            }

            earnings_score = service._feature_aware_score(earnings, 3.0, "Apple earnings guidance", {})
            product_score = service._feature_aware_score(product, 3.0, "Apple earnings guidance", {})

        self.assertGreater(earnings_score, product_score)

    def test_field_reranker_prefers_risk_section_over_earnings_exhibit_for_risk_query(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            features = {"calibrated_signal_score": 2.0, "risk_alert_score": 1.0}
            risk_section = {
                "title": "Apple Inc. 10-K filing filed 2022-10-28 - Item 7 Management’s Discussion and Analysis",
                "source_type": "sec_filing_section",
                "matched_tickers": ["AAPL"],
                "event_tags": ["Company Risk", "10-K"],
            }
            earnings_exhibit = {
                "title": "Apple Inc. 8-K filing filed 2022-11-07 - Exhibit 99.1 Earnings Release / Investor Material",
                "source_type": "sec_filing_exhibit",
                "matched_tickers": ["AAPL"],
                "event_tags": ["Earnings Guidance"],
            }

            risk_score = service._feature_aware_score(risk_section, 3.0, "Apple risk factors", features)
            earnings_score = service._feature_aware_score(earnings_exhibit, 3.0, "Apple risk factors", features)

        self.assertGreater(risk_score, earnings_score)

    def test_field_reranker_prefers_bank_credit_documents_for_bank_sector_query(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            features = {"calibrated_signal_score": 1.0, "risk_alert_score": 1.0}
            bank = {
                "title": "JPMORGAN CHASE & CO 10-K filing filed 2022-02-22 - Item 1A Risk Factors",
                "source_type": "sec_filing_section",
                "matched_tickers": ["JPM"],
                "event_tags": ["Company Risk", "Credit"],
            }
            software = {
                "title": "Salesforce, Inc. 10-Q filing filed 2021-08-27 - Item 1A Risk Factors",
                "source_type": "sec_filing_section",
                "matched_tickers": ["CRM"],
                "event_tags": ["Company Risk", "Consumer Demand"],
            }

            bank_score = service._feature_aware_score(bank, 2.0, "banks credit cycle", features)
            software_score = service._feature_aware_score(software, 2.0, "banks credit cycle", features)

        self.assertGreater(bank_score, software_score)

    def test_source_intent_bonus_prefers_sec_for_filing_queries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            sec_record = {
                "source_type": "sec_filing_section",
                "matched_tickers": ["AAPL"],
                "event_tags": ["10-Q"],
            }
            press_record = {
                "source_type": "company_press_release",
                "matched_tickers": ["AAPL"],
                "event_tags": ["Product Launch"],
            }

            sec_score = service._feature_aware_score(sec_record, 1.0, "Apple filings", {})
            press_score = service._feature_aware_score(press_record, 1.0, "Apple filings", {})

        self.assertGreater(sec_score, press_score)

    def test_folder_order_follows_query_source_intent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            sec_row = service._result_row(
                {
                    "doc_id": "sec",
                    "title": "Apple Inc. 10-Q filing filed 2023-02-03",
                    "canonical_url": "https://sec.gov/sec",
                    "source_type": "sec_filing_section",
                    "available_at": "2022-01-01T16:00:00Z",
                    "published_at": "2022-01-01T16:00:00Z",
                    "matched_tickers": ["AAPL"],
                    "event_tags": ["10-Q"],
                },
                1.0,
                {},
            )
            press_row = service._result_row(
                {
                    "doc_id": "press",
                    "title": "Apple launches a new product",
                    "canonical_url": "https://apple.com/newsroom/press",
                    "source_type": "company_press_release",
                    "available_at": "2023-01-01T16:00:00Z",
                    "published_at": "2023-01-01T16:00:00Z",
                    "matched_tickers": ["AAPL"],
                    "event_tags": ["Product Launch"],
                },
                1.0,
                {},
            )
            macro_row = service._result_row(
                {
                    "doc_id": "macro",
                    "title": "Official US macro release: CBOE VIX Index",
                    "canonical_url": "https://fred.stlouisfed.org/series/VIXCLS",
                    "source_type": "official_macro_release",
                    "available_at": "2023-02-01T16:00:00Z",
                    "published_at": "2023-02-01T16:00:00Z",
                    "matched_tickers": ["MARKET"],
                    "event_tags": ["Official Macro", "Market Volatility"],
                },
                1.0,
                {},
            )

            filing_folders, _ = service._folder_search_results([press_row, sec_row, macro_row], query="Apple filings")
            press_folders, _ = service._folder_search_results([press_row, sec_row, macro_row], query="Apple press release")
            macro_folders, _ = service._folder_search_results([press_row, sec_row, macro_row], query="VIX macro volatility")

        self.assertEqual(filing_folders[0]["folder_key"], "sec_filings")
        self.assertEqual(press_folders[0]["folder_key"], "company_ir")
        self.assertEqual(macro_folders[0]["folder_key"], "macro")

    def test_search_payload_is_paginated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            payload = service.search_payload("", limit=3, offset=0)
            next_payload = service.search_payload("", limit=3, offset=3)

        self.assertEqual(len(payload["results"]), 3)
        self.assertEqual(payload["next_offset"], 3)
        self.assertEqual(next_payload["offset"], 3)
        self.assertNotEqual(payload["results"][0]["doc_id"], next_payload["results"][0]["doc_id"])

    def test_search_payload_prefers_score_over_freshness_for_queries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            old_high_signal = self._result(service, "old", "JPM older risk report", "2014-02-20T16:00:00Z", 9.0)
            fresh_lower_signal = self._result(service, "fresh", "JPM fresh risk update", "2022-02-22T16:00:00Z", 1.0)
            service._search_rows = lambda query: (2, [old_high_signal, fresh_lower_signal])  # type: ignore[method-assign]
            payload = service.search_payload("JPM")

        self.assertEqual(payload["results"][0]["doc_id"], "old")

    def test_search_payload_excludes_live_macro_from_historical_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            historical = self._result(service, "historical", "JPM historical risk report", "2022-02-22T16:00:00Z", 1.0)
            live_macro = self._result(service, "live", "Live macro observation", "2026-05-12T14:00:00Z", 9.0)
            service._search_rows = lambda query: (2, [live_macro, historical])  # type: ignore[method-assign]
            payload = service.search_payload("risk")

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["raw_count"], 1)
        self.assertEqual(payload["results"][0]["doc_id"], "historical")

    def test_official_macro_result_uses_macro_rule_summary_not_zero_signal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            row = service._result_row(
                {
                    "doc_id": "official_macro_bamlh0a0hym2_2022-02-22",
                    "title": "Official US macro release: ICE BofA High Yield Option-Adjusted Spread on 2022-02-22",
                    "body": "Official US macro observation. Series BAMLH0A0HYM2: ICE BofA High Yield Option-Adjusted Spread. Value: 3.2 percent.",
                    "canonical_url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLH0A0HYM2",
                    "source": "FRED / ICE BofA",
                    "source_type": "official_macro_release",
                    "published_at": "2022-02-23T14:00:00Z",
                    "available_at": "2022-02-23T14:00:00Z",
                    "matched_tickers": ["MARKET"],
                    "event_tags": ["official_macro", "credit", "credit_spreads"],
                    "macro_series_id": "BAMLH0A0HYM2",
                    "macro_series_title": "ICE BofA High Yield Option-Adjusted Spread",
                    "macro_family": "credit",
                    "macro_value": 3.2,
                    "macro_units": "percent",
                },
                0.1,
                {},
            )

        self.assertIn("Credit: supportive", row["excerpt"])
        self.assertIn("Risk low", row["excerpt"])
        self.assertNotIn("Signal 0.00", row["excerpt"])
        self.assertEqual(row["macro_rule"]["impact_direction"], "positive")

    def test_official_macro_nearby_dates_group_as_macro_snapshots(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            rows = [
                service._result_row(
                    {
                        "doc_id": "official_macro_vixcls_2023-02-28",
                        "title": "Official US macro release: CBOE VIX Index on 2023-02-28",
                        "body": "Series VIXCLS: CBOE VIX Index. Value: 20.7 index.",
                        "canonical_url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS",
                        "source": "FRED / CBOE",
                        "source_type": "official_macro_release",
                        "published_at": "2023-03-01T14:00:00Z",
                        "available_at": "2023-03-01T14:00:00Z",
                        "matched_tickers": ["MARKET"],
                        "macro_series_id": "VIXCLS",
                        "macro_series_title": "CBOE VIX Index",
                        "macro_family": "market_volatility",
                        "macro_value": 20.7,
                        "macro_units": "index",
                    },
                    0.25,
                    {},
                ),
                service._result_row(
                    {
                        "doc_id": "official_macro_t10y2y_2023-02-27",
                        "title": "Official US macro release: 10-Year Minus 2-Year Treasury Spread on 2023-02-27",
                        "body": "Series T10Y2Y: 10-Year Minus 2-Year Treasury Spread. Value: -0.89 percentage points.",
                        "canonical_url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=T10Y2Y",
                        "source": "FRED",
                        "source_type": "official_macro_release",
                        "published_at": "2023-02-28T14:00:00Z",
                        "available_at": "2023-02-28T14:00:00Z",
                        "matched_tickers": ["MARKET"],
                        "macro_series_id": "T10Y2Y",
                        "macro_series_title": "10-Year Minus 2-Year Treasury Spread",
                        "macro_family": "credit",
                        "macro_value": -0.89,
                        "macro_units": "percentage points",
                    },
                    0.25,
                    {},
                ),
            ]
            grouped, lookup = service._group_search_results(rows)

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["group_title"], "US macro snapshots")
        self.assertEqual(grouped[0]["group_count"], 2)
        self.assertEqual(len(lookup[grouped[0]["group_key"]]), 2)

    def test_search_payload_groups_repeated_filing_titles_and_pages_group(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            rows = [
                self._result(
                    service,
                    "jpm_2014",
                    "JPMORGAN CHASE & CO 10-K filing filed 2014-02-20 - Item 1A RISK FACTORS",
                    "2014-02-20T16:00:00Z",
                    4.0,
                ),
                self._result(
                    service,
                    "jpm_2022",
                    "JPMORGAN CHASE & CO 10-K filing filed 2022-02-22 - Item 1A Risk Factors",
                    "2022-02-22T16:00:00Z",
                    2.0,
                ),
                self._result(
                    service,
                    "jpm_2013",
                    "JPMORGAN CHASE & CO 10-K filing filed 2013-02-28 - Item 1A RISK FACTORS",
                    "2013-02-28T16:00:00Z",
                    5.0,
                ),
            ]
            service._search_rows = lambda query: (3, rows)  # type: ignore[method-assign]
            payload = service.search_payload("JPM")
            group_key = payload["results"][0]["group_key"]
            group_payload = service.search_payload("JPM", group_key=group_key, limit=2)

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["raw_count"], 3)
        self.assertEqual(payload["results"][0]["doc_id"], "jpm_2022")
        self.assertEqual(payload["results"][0]["group_count"], 3)
        self.assertEqual([row["doc_id"] for row in group_payload["results"]], ["jpm_2022", "jpm_2014"])
        self.assertTrue(group_payload["group_mode"])
        self.assertEqual(group_payload["next_offset"], 2)

    def test_search_payload_folders_many_ticker_groups(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            rows = [
                self._result(service, "jpm_8k", "JPMORGAN CHASE & CO 8-K filing filed 2023-02-22", "2023-02-22T16:00:00Z", 1.0),
                self._result(service, "jpm_10k", "JPMORGAN CHASE & CO 10-K filing filed 2023-02-21", "2023-02-21T16:00:00Z", 1.0),
                self._result(service, "jpm_10q", "JPMORGAN CHASE & CO 10-Q filing filed 2022-11-01", "2022-11-01T16:00:00Z", 1.0),
            ]
            with patch.object(service, "_search_rows", return_value=(len(rows), rows)):
                payload = service.search_payload("JPM")
                payload_by_name = service.search_payload("JPMorgan Chase")
                analysis = service.analyze_search_folder({"query": "JPM", "folder_key": "sec_filings"})

        self.assertTrue(payload["foldered"])
        self.assertTrue(payload_by_name["foldered"])
        self.assertEqual(payload["results"][0]["result_kind"], "folder")
        self.assertEqual(payload_by_name["results"][0]["result_kind"], "folder")
        self.assertEqual(payload["results"][0]["folder_key"], "sec_filings")
        self.assertEqual(payload["results"][0]["folder_document_count"], 3)
        self.assertEqual(len(analysis["suggested_charts"]), 3)
        self.assertIn("chart_pack", analysis)
        self.assertEqual(len(analysis["chart_pack"]["charts"]), 3)
        self.assertNotIn("analysis_markdown", analysis)
        self.assertEqual(analysis["folder_title"], "SEC filings")

    def test_search_folder_analysis_window_can_expand(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            rows = [
                self._result(service, "jpm_8k_2023", "JPMORGAN CHASE & CO 8-K filing filed 2023-02-22", "2023-02-22T16:00:00Z", 1.0),
                self._result(service, "jpm_10k_2023", "JPMORGAN CHASE & CO 10-K filing filed 2023-02-21", "2023-02-21T16:00:00Z", 1.0),
                self._result(service, "jpm_10q_2022", "JPMORGAN CHASE & CO 10-Q filing filed 2022-11-01", "2022-11-01T16:00:00Z", 1.0),
                self._result(service, "jpm_10k_2017", "JPMORGAN CHASE & CO 10-K filing filed 2017-02-28", "2017-02-28T16:00:00Z", 1.0),
            ]
            with patch.object(service, "_search_rows", return_value=(len(rows), rows)):
                one_year = service.analyze_search_folder({"query": "JPM", "folder_key": "sec_filings", "window": "1y"})
                all_time = service.analyze_search_folder({"query": "JPM", "folder_key": "sec_filings", "window": "all"})

        self.assertEqual(one_year["window"], "1y")
        self.assertEqual(one_year["window_label"], "1Y")
        self.assertEqual(one_year["analyzed_document_count"], 3)
        self.assertEqual(all_time["window"], "all")
        self.assertEqual(all_time["window_label"], "All time")
        self.assertEqual(all_time["analyzed_document_count"], 4)

    def test_search_folder_llm_returns_chart_data_only(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                chart_json = {
                    "chart_pack": {
                        "charts": [
                            {
                                "title": "LLM Risk Trend",
                                "subtitle": "structured chart data",
                                "series": [
                                    {
                                        "label": "Risk",
                                        "unit": "score",
                                        "points": [
                                            {"date": "2023-01-01", "value": 0.1},
                                            {"date": "2023-02-01", "value": 0.4},
                                        ],
                                    }
                                ],
                            }
                        ]
                    }
                }
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(chart_json),
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmpdir, patch("web_app.urllib.request.urlopen", return_value=FakeResponse()):
            service = self._service(tmpdir)
            rows = [
                self._result(service, "jpm_8k", "JPMORGAN CHASE & CO 8-K filing filed 2023-02-22", "2023-02-22T16:00:00Z", 1.0),
                self._result(service, "jpm_10k", "JPMORGAN CHASE & CO 10-K filing filed 2023-02-21", "2023-02-21T16:00:00Z", 1.0),
                self._result(service, "jpm_10q", "JPMORGAN CHASE & CO 10-Q filing filed 2022-11-01", "2022-11-01T16:00:00Z", 1.0),
            ]
            with patch.object(service, "_search_rows", return_value=(len(rows), rows)):
                analysis = service.analyze_search_folder(
                    {
                        "query": "JPM",
                        "folder_key": "sec_filings",
                        "force_llm": True,
                        "llm": {"api_key": "secret", "model": "mistral-small-latest"},
                    }
                )

        self.assertTrue(analysis["llm_used"])
        self.assertEqual(analysis["chart_pack"]["source"], "llm")
        self.assertEqual(analysis["chart_pack"]["charts"][0]["title"], "LLM Risk Trend")
        self.assertNotIn("analysis_markdown", analysis)
        self.assertNotIn("secret", json.dumps(analysis))

    def test_search_folder_analysis_skips_llm_by_default_for_demo_speed(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("web_app.urllib.request.urlopen") as mocked_urlopen:
            service = self._service(tmpdir)
            rows = [
                self._result(service, "ba_8k", "BOEING CO 8-K filing filed 2023-02-22", "2023-02-22T16:00:00Z", 1.0),
                self._result(service, "ba_10k", "BOEING CO 10-K filing filed 2023-02-21", "2023-02-21T16:00:00Z", 1.0),
                self._result(service, "ba_10q", "BOEING CO 10-Q filing filed 2022-11-01", "2022-11-01T16:00:00Z", 1.0),
            ]
            with patch.object(service, "_search_rows", return_value=(len(rows), rows)):
                first = service.analyze_search_folder(
                    {
                        "query": "Boeing",
                        "folder_key": "sec_filings",
                        "window": "1y",
                        "llm": {"api_key": "secret", "model": "mistral-small-latest"},
                    }
                )
                second = service.analyze_search_folder(
                    {
                        "query": "Boeing",
                        "folder_key": "sec_filings",
                        "window": "1y",
                        "llm": {"api_key": "secret", "model": "mistral-small-latest"},
                    }
                )
                ticker_variant = service.analyze_search_folder(
                    {
                        "query": "BA",
                        "folder_key": "sec_filings",
                        "window": "1y",
                        "llm": {"api_key": "secret", "model": "mistral-small-latest"},
                    }
                )

        mocked_urlopen.assert_not_called()
        self.assertFalse(first["llm_used"])
        self.assertEqual(first["model"], "precomputed-local")
        self.assertEqual(first["cache_status"], "computed-local")
        self.assertEqual(second["cache_status"], "precomputed")
        self.assertEqual(ticker_variant["cache_status"], "precomputed")
        self.assertEqual(ticker_variant["query"], "BA")
        self.assertEqual(ticker_variant["query_identity"], first["query_identity"])
        self.assertIn("llm_skip_reason", first)

    def test_search_folder_analysis_returns_json_when_folder_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            with (
                patch.object(service, "_search_rows", return_value=(0, [])),
                patch.object(service, "_direct_folder_analysis_docs", return_value=[]),
            ):
                analysis = service.analyze_search_folder({"query": "NVDA", "folder_key": "sec_filings", "window": "all"})

        self.assertEqual(analysis["folder_key"], "sec_filings")
        self.assertEqual(analysis["folder_title"], "SEC filings")
        self.assertEqual(analysis["document_count"], 0)
        self.assertEqual(analysis["analyzed_document_count"], 0)
        self.assertEqual(analysis["cache_status"], "computed-local")

    def test_llm_analyst_view_parser_accepts_structured_dashboard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            parsed = service._extract_llm_analyst_view(
                json.dumps(
                    {
                        "analyst_view": {
                            "title": "Financial statement snapshot",
                            "source_document": {"title": "AAPL 10-Q", "available_at": "2023-02-03T16:00:00Z", "source_type": "sec_filing_section"},
                            "metric_cards": [{"label": "Revenue", "value": "$117.2B", "delta": "-5.5%", "tone": "negative"}],
                            "tables": [{"title": "Key numbers", "rows": [["Revenue", "$117.2B", "$123.9B", "-5.5%", "negative"]]}],
                            "charts": [
                                {
                                    "type": "compare_bars",
                                    "title": "Income Statement",
                                    "rows": [
                                        {
                                            "metric": "Revenue",
                                            "current": 117154,
                                            "prior": 123945,
                                            "current_label": "$117.2B",
                                            "prior_label": "$123.9B",
                                            "change_label": "-5.5%",
                                            "tone": "negative",
                                        }
                                    ],
                                }
                            ],
                        }
                    }
                )
            )

        self.assertTrue(parsed["available"])
        self.assertEqual(parsed["source"], "llm")
        self.assertEqual(parsed["metric_cards"][0]["label"], "Revenue")
        self.assertEqual(parsed["charts"][0]["type"], "compare_bars")

    def test_sec_sections_from_same_accession_group_as_one_filing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            rows = [
                self._result(
                    service,
                    "sec_hd_8k_000035495023000051__item_9_01_financial_statements_exhibits",
                    "HOME DEPOT, INC. 8-K filing filed 2023-02-28 - Item 9.01 Financial Statements and Exhibits",
                    "2023-02-28T16:00:00Z",
                    1.0,
                ),
                self._result(
                    service,
                    "sec_hd_8k_000035495023000051__item_5_03_articles_bylaws_or_fiscal_year",
                    "HOME DEPOT, INC. 8-K filing filed 2023-02-28 - Item 5.03 Amendments to Articles of Incorporation or Bylaws; Change in Fiscal Year",
                    "2023-02-28T16:00:00Z",
                    1.0,
                ),
                self._result(
                    service,
                    "sec_hd_8k_000035495023000051__item_5_02_director_or_officer_changes",
                    "HOME DEPOT, INC. 8-K filing filed 2023-02-28 - Item 5.02 Departure of Directors or Certain Officers; Election of Directors; Appointment of Certain Officers; Compensatory Arrangements of Certain Officers",
                    "2023-02-28T16:00:00Z",
                    1.0,
                ),
            ]
            grouped, lookup = service._group_search_results(rows)

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["group_title"], "HOME DEPOT, INC. 8-K filing filed 2023-02-28")
        self.assertEqual(grouped[0]["group_count"], 3)
        self.assertEqual(len(lookup[grouped[0]["group_key"]]), 3)

    def test_toggle_favorite_keeps_current_order_until_refresh(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            service.save_settings(
                {
                    "portfolio": [{"ticker": "AAPL", "purchase_price": 100, "quantity": 1}],
                    "favorite_websites": ["https://example.com/"],
                }
            )
            results = service.search_payload("supplier concerns")["results"]
            titles_before = [row["title"] for row in results]
            toggled = service.toggle_favorite_payload(results, "https://example.com/doc_000001")
            titles_after = [row["title"] for row in toggled["results"]]

        self.assertEqual(titles_after, titles_before)
        self.assertEqual(toggled["results"][0]["favorite_status"], "pending_removed")
        self.assertEqual(toggled["results"][0]["favorite_icon"], "empty")

    def test_my_vibe_posts_hide_full_text_but_analysis_uses_it(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch("web_app.server_llm_secret", return_value=""):
            service = self._service(tmpdir)
            service.save_settings(
                {
                    "portfolio": [{"ticker": "AAPL", "purchase_price": 100, "quantity": 1}],
                    "favorite_websites": ["https://example.com/"],
                }
            )
            posts = service.my_vibe_posts("example.com")["posts"]
            analysis = service.analyze_my_vibe_post({"post_id": posts[0]["id"], "llm": {}})

        self.assertGreater(len(posts), 0)
        self.assertNotIn("text", posts[0])
        self.assertIn("text_char_count", posts[0])
        self.assertFalse(analysis["llm_used"])
        self.assertFalse(analysis["api_key_persisted"])
        self.assertNotIn("text", analysis["post"])
        self.assertGreater(analysis["post"]["text_char_count"], 0)

    def test_my_vibe_analysis_calls_remote_llm_when_key_is_supplied(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "## Short conclusion\nLLM result from provider.\n\n## Confidence\nmedium"
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["authorization"] = request.headers.get("Authorization")
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmpdir, patch("web_app.urllib.request.urlopen", side_effect=fake_urlopen):
            service = self._service(tmpdir)
            service.save_settings(
                {
                    "portfolio": [{"ticker": "AAPL", "purchase_price": 100, "quantity": 1}],
                    "favorite_websites": ["https://example.com/"],
                }
            )
            post_id = service.my_vibe_posts("example.com")["posts"][0]["id"]
            analysis = service.analyze_my_vibe_post(
                {
                    "post_id": post_id,
                    "llm": {
                        "api_key": "secret",
                        "model": "mistral-small-latest",
                        "base_url": "https://api.mistral.ai/v1/chat/completions",
                    },
                }
            )

        self.assertTrue(analysis["llm_used"])
        self.assertTrue(analysis["api_key_received"])
        self.assertFalse(analysis["api_key_persisted"])
        self.assertIn("LLM result from provider", analysis["analysis_markdown"])
        self.assertEqual(captured["url"], "https://api.mistral.ai/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "mistral-small-latest")
        self.assertNotIn("secret", json.dumps(analysis))

    def test_local_analysis_only_marks_direct_company_holdings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            service.save_settings(
                {
                    "portfolio": [
                        {"ticker": "MSFT", "purchase_price": 100, "quantity": 1},
                        {"ticker": "CVX", "purchase_price": 100, "quantity": 1},
                    ],
                    "favorite_websites": ["https://example.com/"],
                }
            )
            post = {
                "id": "manual",
                "title": "Microsoft filing risk",
                "url": "https://sec.gov/manual",
                "text": "Microsoft software quality risk and regulatory scrutiny.",
                "matched_tickers": ["MSFT"],
                "source_type": "sec_filing_section",
            }
            portfolio_summary = summarize_portfolio(service.load_settings()["portfolio"])
            prompt = build_portfolio_impact_prompt(post, portfolio_summary, service.macro_snapshot or {})
            analysis = service._local_analysis_payload(post, portfolio_summary, prompt, False)

        self.assertEqual([row["ticker"] for row in analysis["affected_holdings"]], ["MSFT"])

    def test_my_vibe_posts_are_paginated_in_five_item_batches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = self._service(tmpdir)
            service.save_settings(
                {
                    "portfolio": [{"ticker": "AAPL", "purchase_price": 100, "quantity": 1}],
                    "favorite_websites": ["https://example.com/"],
                }
            )
            first_page = service.my_vibe_posts("example.com", limit=5, offset=0)
            second_page = service.my_vibe_posts("example.com", limit=5, offset=5)

        self.assertEqual(len(first_page["posts"]), 5)
        self.assertEqual(first_page["next_offset"], 5)
        self.assertEqual(len(second_page["posts"]), 5)
        self.assertEqual(second_page["offset"], 5)
        self.assertNotEqual(first_page["posts"][0]["id"], second_page["posts"][0]["id"])
        ranked_scores = [row["vibe_score"] for row in first_page["posts"] + second_page["posts"]]
        self.assertEqual(ranked_scores, sorted(ranked_scores, reverse=True))

    def test_public_demo_settings_are_cookie_session_isolated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = FinPortfolioWebService(
                settings_path=Path(tmpdir) / "settings.json",
                documents_path=ROOT / "data" / "processed_documents" / "documents.jsonl",
                public_demo=True,
                demo_settings_dir=Path(tmpdir) / "demo_sessions",
            )
            service._request_context.session_id = "session_a"
            service.save_settings(
                {
                    "portfolio": [{"ticker": "AAPL", "purchase_price": 100, "quantity": 1}],
                    "favorite_websites": ["https://example.com/"],
                }
            )
            service._request_context.session_id = "session_b"
            service.save_settings(
                {
                    "portfolio": [{"ticker": "MSFT", "purchase_price": 200, "quantity": 2}],
                    "favorite_websites": ["https://sec.gov/"],
                }
            )
            service._request_context.session_id = "session_a"
            settings_a = service.load_settings()
            service._request_context.session_id = "session_b"
            settings_b = service.load_settings()

        self.assertEqual(settings_a["portfolio"][0]["ticker"], "AAPL")
        self.assertEqual(settings_b["portfolio"][0]["ticker"], "MSFT")
        self.assertEqual(settings_a["favorite_websites"], ["https://example.com/"])
        self.assertEqual(settings_b["favorite_websites"], ["https://sec.gov/"])


class WebAppHTTPTests(unittest.TestCase):
    def test_dashboard_endpoint_returns_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            server = build_server(
                "127.0.0.1",
                0,
                Path(tmpdir) / "settings.json",
                ROOT / "data" / "processed_documents" / "documents.jsonl",
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                conn.request("GET", "/api/dashboard")
                response = conn.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["language"], "en")
        self.assertEqual(len(payload["macro_dashboard"]["what_matters_cards"]), 3)

    def test_document_route_serves_html_viewer_not_download(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_path = Path(tmpdir) / "docs.jsonl"
            docs_path.write_text(
                json.dumps(
                    {
                        "doc_id": "doc/with space",
                        "title": "Apple Inc. 10-Q filing filed 2023-02-03",
                        "body": "Item 1A. Risk Factors Full document body.",
                        "canonical_url": "https://www.sec.gov/example",
                        "source_type": "sec_filing_section",
                        "available_at": "2023-02-03T16:00:00Z",
                        "matched_tickers": ["AAPL"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch("web_app.server_llm_secret", return_value=""):
                server = build_server("127.0.0.1", 0, Path(tmpdir) / "settings.json", docs_path)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
                    conn.request("GET", f"/document/{quote('doc/with space', safe='')}")
                    response = conn.getresponse()
                    body = response.read().decode("utf-8")
                    content_type = response.getheader("content-type")
                    content_disposition = response.getheader("content-disposition")
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

        self.assertEqual(response.status, 200)
        self.assertEqual(content_type, "text/html; charset=utf-8")
        self.assertIsNone(content_disposition)
        self.assertIn("Apple Inc. 10-Q filing", body)
        self.assertIn("Document Brief", body)
        self.assertIn("Original cached text", body)
        self.assertIn("Item 1A. Risk Factors Full document body.", body)


if __name__ == "__main__":
    unittest.main()
