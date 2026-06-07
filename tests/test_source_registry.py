from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawler.source_registry import (
    build_url_health_record,
    canonicalize_url,
    enrich_record_source_metadata,
    load_source_registry,
)
from finportfolio_ir.schema import FinancialDocument


class SourceRegistryTests(unittest.TestCase):
    def test_canonicalize_url_removes_tracking_and_fragment(self):
        canonical = canonicalize_url("HTTPS://www.sec.gov/ixviewer/doc.htm?utm_source=x&doc=1#section")

        self.assertEqual(canonical, "https://www.sec.gov/ixviewer/doc.htm?doc=1")

    def test_registry_metadata_enriches_document_without_breaking_schema(self):
        registry = load_source_registry(ROOT / "data" / "source_registry" / "source_registry.csv")
        record = enrich_record_source_metadata(
            {
                "doc_id": "sec1",
                "title": "Apple 10-Q",
                "body": "Apple filed its quarterly report.",
                "source": "sec_edgar",
                "url": "https://www.sec.gov/Archives/edgar/data/320193/form10q.htm?utm_campaign=x",
                "published_at": "2022-03-15T12:00:00Z",
                "available_at": "2022-03-15T12:01:00Z",
            },
            registry,
        )
        document = FinancialDocument.from_dict(record)

        self.assertEqual(document.source_registry_id, "sec_edgar")
        self.assertEqual(document.source_reliability_tier, "official")
        self.assertEqual(document.source_type, "sec_filing")
        self.assertTrue(document.canonical_url.endswith("/Archives/edgar/data/320193/form10q.htm"))

    def test_failed_url_health_is_explicit(self):
        health = build_url_health_record(
            "https://example.com/missing",
            error="timeout",
            checked_at="2022-03-15T12:00:00Z",
        )

        self.assertEqual(health["fetch_status"], "failed")
        self.assertEqual(health["error"], "timeout")
        self.assertEqual(health["last_url_check_at"], "2022-03-15T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
