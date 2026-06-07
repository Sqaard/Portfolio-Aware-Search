import unittest

from crawler.sec_section_parser import extract_sec_sections, html_to_sec_text
from features.build_sec_full_section_corpus import (
    _exhibit_id_from_name,
    _textual_exhibit_items,
    build_exhibit_record_for_filing,
    build_section_records_for_filing,
)
from features.build_sec_section_contexts import select_representative_sections


class SecSectionParserTests(unittest.TestCase):
    def test_10k_parser_ignores_toc_and_keeps_actual_item_sections(self):
        html = """
        <html><body>
        <div>Item 1A. Risk Factors</div>
        <div>Item 7. Management's Discussion and Analysis</div>
        <div>Item 8. Financial Statements and Supplementary Data</div>
        <h2>Item 1A. Risk Factors</h2>
        <p>Actual risk paragraph about liquidity, demand, regulation, and competition.</p>
        <p>{risk_body}</p>
        <h2>Item 1B. Unresolved Staff Comments</h2>
        <p>None.</p>
        <h2>Item 7. Management's Discussion and Analysis</h2>
        <p>Actual MD&A paragraph about revenue, margins, and cash flow.</p>
        <p>{mda_body}</p>
        <h2>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</h2>
        <p>Market risk body.</p>
        <h2>Item 8. Financial Statements and Supplementary Data</h2>
        <p>Actual financial statement body.</p>
        <p>{financial_body}</p>
        <h2>Item 9. Changes in and Disagreements</h2>
        <p>End.</p>
        </body></html>
        """.format(
            risk_body="risk " * 180,
            mda_body="management discussion " * 120,
            financial_body="financial statement " * 120,
        )

        sections = extract_sec_sections(html_to_sec_text(html), "10-K")
        by_id = {section.section_id: section for section in sections}

        self.assertIn("item_1a_risk_factors", by_id)
        self.assertIn("item_7_mda", by_id)
        self.assertIn("item_8_financial_statements", by_id)
        self.assertIn("Actual risk paragraph", by_id["item_1a_risk_factors"].body)
        self.assertGreater(len(by_id["item_1a_risk_factors"].body), 500)

    def test_8k_parser_extracts_numbered_current_report_items(self):
        html = """
        <html><body>
        <h1>Item 2.02 Results of Operations and Financial Condition.</h1>
        <p>Registrant released quarterly results and guidance.</p>
        <h1>Item 9.01 Financial Statements and Exhibits.</h1>
        <p>Exhibit 99.1 press release.</p>
        </body></html>
        """

        sections = extract_sec_sections(html_to_sec_text(html), "8-K")
        ids = [section.section_id for section in sections]

        self.assertTrue(any(item.startswith("item_2_02") for item in ids))
        self.assertTrue(any(item.startswith("item_9_01") for item in ids))

    def test_section_record_preserves_authoritative_sec_ticker(self):
        base = {
            "doc_id": "sec_aapl_10k",
            "title": "Apple 10-K",
            "source": "SEC EDGAR",
            "source_type": "sec_filing",
            "url": "https://www.sec.gov/example",
            "canonical_url": "https://www.sec.gov/example",
            "published_at": "2020-10-30T20:00:00Z",
            "first_seen_at": "2020-10-30T20:00:00Z",
            "available_at": "2020-10-30T20:00:00Z",
            "ingested_at": "2026-05-13T00:00:00Z",
            "last_url_check_at": "2026-05-13T00:00:00Z",
            "version_id": "0000320193-20-000096",
            "duplicate_cluster_id": "000032019320000096",
            "matched_tickers": ["MARKET", "AAPL"],
            "matched_holdings": ["MARKET", "AAPL"],
            "sec_form": "10-K",
            "sec_ticker": "AAPL",
            "sec_accession_number": "0000320193-20-000096",
            "split": "train",
        }
        html = """
        <h2>Item 1A. Risk Factors</h2>
        <p>{body}</p>
        <h2>Item 1B. Unresolved Staff Comments</h2>
        """.format(body="risk " * 120)

        records = build_section_records_for_filing(
            base,
            html,
            fetch_status="cached_full",
            downloaded_bytes=len(html),
            max_section_chars=250000,
        )

        self.assertEqual(records[0]["matched_tickers"], ["AAPL"])
        self.assertEqual(records[0]["matched_holdings"], ["AAPL"])
        self.assertEqual(records[0]["sec_section_id"], "item_1a_risk_factors")
        self.assertEqual(records[0]["parent_doc_id"], "sec_aapl_10k")

    def test_representative_section_prefers_mda_for_10k(self):
        rows = [
            {
                "doc_id": "p1__item_1a",
                "parent_doc_id": "p1",
                "sec_form": "10-K",
                "sec_section_id": "item_1a_risk_factors",
                "sec_section_chars": 9000,
                "matched_tickers": ["AAPL"],
                "available_at": "2020-01-01T00:00:00Z",
                "split": "train",
            },
            {
                "doc_id": "p1__item_7",
                "parent_doc_id": "p1",
                "sec_form": "10-K",
                "sec_section_id": "item_7_mda",
                "sec_section_chars": 5000,
                "matched_tickers": ["AAPL"],
                "available_at": "2020-01-01T00:00:00Z",
                "split": "train",
            },
        ]

        selected = select_representative_sections(rows)

        self.assertEqual([row["doc_id"] for row in selected], ["p1__item_7"])

    def test_exhibit_id_parses_common_sec_names(self):
        self.assertEqual(_exhibit_id_from_name("a8-kex991q4202109252021.htm"), "exhibit_99_1")
        self.assertEqual(_exhibit_id_from_name("d123456dex101.htm"), "exhibit_10_1")
        self.assertEqual(_exhibit_id_from_name("ex-99.2.htm"), "exhibit_99_2")
        self.assertEqual(_exhibit_id_from_name("ex991-930.htm"), "exhibit_99_1")
        self.assertEqual(_exhibit_id_from_name("ex101_364.htm"), "exhibit_10_1")

    def test_textual_exhibit_items_exclude_primary_and_support_files(self):
        payload = {
            "directory": {
                "item": [
                    {"name": "aapl-20211028.htm"},
                    {"name": "a8-kex991q4202109252021.htm"},
                    {"name": "r1.htm"},
                    {"name": "aapl-20211028_def.xml"},
                    {"name": "0000320193-21-000104-index.html"},
                ]
            }
        }

        items = _textual_exhibit_items(payload, primary_document="aapl-20211028.htm", max_exhibits=6)

        self.assertEqual([item["name"] for item in items], ["a8-kex991q4202109252021.htm"])

    def test_8k_representative_section_prefers_exhibit_99(self):
        rows = [
            {
                "doc_id": "p1__item_2_02",
                "parent_doc_id": "p1",
                "source_type": "sec_filing_section",
                "sec_form": "8-K",
                "sec_section_id": "item_2_02_results_operations_financial_condition",
                "sec_section_chars": 2000,
                "matched_tickers": ["AAPL"],
                "available_at": "2021-01-01T00:00:00Z",
                "split": "test",
            },
            {
                "doc_id": "p1__exhibit_99_1",
                "parent_doc_id": "p1",
                "source_type": "sec_filing_exhibit",
                "sec_form": "8-K",
                "sec_section_id": "exhibit_99_1",
                "sec_section_chars": 12000,
                "matched_tickers": ["AAPL"],
                "available_at": "2021-01-01T00:00:00Z",
                "split": "test",
            },
        ]

        selected = select_representative_sections(rows)

        self.assertEqual([row["doc_id"] for row in selected], ["p1__exhibit_99_1"])

    def test_8k_representative_section_does_not_prefer_generic_exhibit(self):
        rows = [
            {
                "doc_id": "p1__item_2_02",
                "parent_doc_id": "p1",
                "source_type": "sec_filing_section",
                "sec_form": "8-K",
                "sec_section_id": "item_2_02_results_operations_financial_condition",
                "sec_section_chars": 2000,
                "matched_tickers": ["AAPL"],
                "available_at": "2021-01-01T00:00:00Z",
                "split": "test",
            },
            {
                "doc_id": "p1__exhibit_5_1",
                "parent_doc_id": "p1",
                "source_type": "sec_filing_exhibit",
                "sec_form": "8-K",
                "sec_section_id": "exhibit_5_1",
                "sec_section_chars": 12000,
                "matched_tickers": ["AAPL"],
                "available_at": "2021-01-01T00:00:00Z",
                "split": "test",
            },
        ]

        selected = select_representative_sections(rows)

        self.assertEqual([row["doc_id"] for row in selected], ["p1__item_2_02"])

    def test_exhibit_record_preserves_source_url_and_parent(self):
        base = {
            "doc_id": "sec_aapl_8k",
            "title": "Apple 8-K",
            "source": "SEC EDGAR",
            "source_type": "sec_filing",
            "url": "https://www.sec.gov/Archives/edgar/data/1/0001/aapl.htm",
            "canonical_url": "https://www.sec.gov/Archives/edgar/data/1/0001/aapl.htm",
            "published_at": "2021-10-28T20:00:00Z",
            "first_seen_at": "2021-10-28T20:00:00Z",
            "available_at": "2021-10-28T20:00:00Z",
            "ingested_at": "2026-05-13T00:00:00Z",
            "last_url_check_at": "2026-05-13T00:00:00Z",
            "version_id": "0001",
            "duplicate_cluster_id": "0001",
            "sec_form": "8-K",
            "sec_ticker": "AAPL",
            "sec_accession_number": "0001",
            "split": "test",
        }

        record = build_exhibit_record_for_filing(
            base,
            exhibit_item={"name": "a8-kex991q4202109252021.htm", "size": "176492"},
            exhibit_html="<html><body><p>Revenue and earnings release text.</p></body></html>",
            exhibit_url="https://www.sec.gov/Archives/edgar/data/1/0001/a8-kex991q4202109252021.htm",
            fetch_status="cached_full",
            downloaded_bytes=100,
            max_section_chars=250000,
            ordinal=10001,
        )

        self.assertEqual(record["source_type"], "sec_filing_exhibit")
        self.assertEqual(record["sec_exhibit_id"], "exhibit_99_1")
        self.assertEqual(record["parent_doc_id"], "sec_aapl_8k")
        self.assertEqual(record["matched_holdings"], ["AAPL"])
        self.assertEqual(record["canonical_url"], "https://www.sec.gov/Archives/edgar/data/1/0001/a8-kex991q4202109252021.htm")


if __name__ == "__main__":
    unittest.main()
