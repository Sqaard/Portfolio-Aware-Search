from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.build_evidence_units import build_evidence_units
from finportfolio_ir.schema import FinancialDocument


class EvidenceUnitTests(unittest.TestCase):
    def test_sec_section_remains_schema_compatible_evidence_unit(self):
        records = [
            {
                "doc_id": "sec_aapl_10q__part2_item_1a_risk_factors",
                "parent_doc_id": "sec_aapl_10q",
                "title": "Apple 10-Q - Item 1A Risk Factors",
                "body": "Risk factors include supply chain and demand uncertainty.",
                "source": "sec_edgar",
                "source_type": "sec_filing_section",
                "source_registry_id": "sec_edgar",
                "url": "https://www.sec.gov/Archives/test.htm",
                "published_at": "2022-03-15T12:00:00Z",
                "available_at": "2022-03-15T12:00:00Z",
                "tickers_detected": ["AAPL"],
                "sec_form": "10-Q",
                "sec_section_id": "part2_item_1a_risk_factors",
                "sec_section_title": "Risk Factors",
                "sec_section_ordinal": 2,
            }
        ]

        units = build_evidence_units(records)

        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["evidence_unit_type"], "sec_section")
        self.assertEqual(units[0]["evidence_unit_claim_type"], "risk_factors")
        self.assertEqual(units[0]["parent_doc_id"], "sec_aapl_10q")
        FinancialDocument.from_dict(units[0])

    def test_company_ir_is_split_into_fact_blocks(self):
        body = (
            "Apple announced a new product launch with improved performance. "
            "Revenue grew 8 percent year over year and gross margin expanded. "
            "Management expects services demand to remain resilient in the next quarter. "
            "The company also noted supply chain risk in several markets."
        )
        records = [
            {
                "doc_id": "company_aapl_release",
                "title": "Apple announces product update",
                "body": body,
                "source": "apple.com",
                "source_type": "company_press_release",
                "source_registry_id": "company_ir",
                "url": "https://www.apple.com/newsroom/test",
                "published_at": "2022-03-15T12:00:00Z",
                "available_at": "2022-03-15T12:00:00Z",
                "tickers_detected": ["AAPL"],
            }
        ]

        units = build_evidence_units(records, company_max_chars=120, company_min_chars=40)

        self.assertGreaterEqual(len(units), 2)
        self.assertTrue(all(unit["evidence_unit_type"] == "company_ir_fact_block" for unit in units))
        self.assertTrue(any(unit["evidence_unit_claim_type"] == "company_financial_fact" for unit in units))
        self.assertTrue(all(unit["parent_doc_id"] == "company_aapl_release" for unit in units))
        FinancialDocument.from_dict(units[0])

    def test_macro_observation_keeps_macro_fields(self):
        records = [
            {
                "doc_id": "official_macro_dgs10_2022-03-15",
                "title": "Official US macro release: 10-Year Treasury Yield on 2022-03-15",
                "body": "Official US macro observation. Value: 2.1 percent.",
                "source": "FRED",
                "source_type": "official_macro_release",
                "source_registry_id": "fred",
                "url": "https://fred.stlouisfed.org/series/DGS10",
                "published_at": "2022-03-16T14:00:00Z",
                "available_at": "2022-03-16T14:00:00Z",
                "tickers_detected": ["MARKET"],
                "macro_series_id": "DGS10",
                "macro_family": "rates",
                "macro_observation_date": "2022-03-15",
                "macro_value": 2.1,
            }
        ]

        units = build_evidence_units(records)

        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["evidence_unit_type"], "macro_observation")
        self.assertEqual(units[0]["evidence_unit_claim_type"], "rates")
        self.assertEqual(units[0]["macro_series_id"], "DGS10")
        FinancialDocument.from_dict(units[0])


if __name__ == "__main__":
    unittest.main()
