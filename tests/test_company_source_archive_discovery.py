import tempfile
import json
from pathlib import Path
import unittest

from crawler.company_source_archive_discovery import (
    DiscoveryConfig,
    FetchResult,
    classify_vendor_profile,
    decode_response_text,
    discover_documents_from_sources,
    should_try_q4_feed,
)


class CompanySourceArchiveDiscoveryTests(unittest.TestCase):
    def test_discovers_validated_dated_detail_document(self):
        pages = {
            "https://example.com/news/": """
                <html><head><title>News Archive</title></head>
                <body><a href="/news/2021/">2021</a></body></html>
            """,
            "https://example.com/news/2021": """
                <html><body>
                <a href="/news/2021/05/01/apple-reports-quarterly-results/">
                Apple reports quarterly results May 1, 2021</a>
                </body></html>
            """,
            "https://example.com/news/2021/": """
                <html><body>
                <a href="/news/2021/05/01/apple-reports-quarterly-results/">
                Apple reports quarterly results May 1, 2021</a>
                </body></html>
            """,
            "https://example.com/news/2021/05/01/apple-reports-quarterly-results/": """
                <html>
                <head>
                <title>Apple reports quarterly results</title>
                <meta property="article:published_time" content="2021-05-01T13:00:00Z" />
                </head>
                <body>
                <article>
                <p>Apple today announced quarterly financial results and reported revenue growth.</p>
                <p>The company discussed earnings, guidance, demand trends, margin performance,
                supply chain conditions, cash flow, services revenue, products, customers,
                investment plans, and risks that may affect future results.</p>
                <p>Management said the business remains focused on innovation, operating discipline,
                capital return, and long-term shareholder value. This release includes enough
                full-text content for a validated company official document.</p>
                </article>
                </body>
                </html>
            """,
        }

        def fetcher(url):
            normalized = url.rstrip("/") + "/"
            text = pages.get(url) or pages.get(normalized) or ""
            return FetchResult(
                url=url,
                final_url=url,
                status_code=200 if text else 404,
                content_type="text/html",
                text=text,
                error="" if text else "missing",
            )

        with tempfile.TemporaryDirectory() as tmp:
            metadata = Path(tmp) / "metadata.csv"
            metadata.write_text(
                "ticker,cik,official_name,company_name,common_name,sector,aliases,source_credibility\n"
                "AAPL,0000320193,Apple Inc.,Apple Inc.,Apple Inc.,Information Technology,Apple|AAPL,0.9\n",
                encoding="utf-8",
            )
            documents, detail_manifest, source_manifest, vendor_queue = discover_documents_from_sources(
                [
                    {
                        "ticker": "AAPL",
                        "company": "Apple",
                        "source_type": "company_news_archive",
                        "url": "https://example.com/news/",
                        "crawler_grade": "crawler_ready",
                    }
                ],
                metadata_path=metadata,
                config=DiscoveryConfig(start_year=2021, end_year=2021, min_body_words=40, sleep_seconds=0),
                fetcher=fetcher,
            )

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["matched_tickers"], ["AAPL"])
        self.assertEqual(documents[0]["published_at"], "2021-05-01T13:00:00Z")
        self.assertEqual(documents[0]["source_type"], "company_press_release")
        self.assertEqual(source_manifest[0]["accepted_documents"], 1)
        self.assertTrue(any(row["accepted"] == "yes" for row in detail_manifest))
        self.assertEqual(vendor_queue, [])

    def test_classifies_q4_like_source_for_vendor_queue(self):
        profile, recommendation = classify_vendor_profile(
            "https://investor.example.com/news/default.aspx",
            "<script>var Q4ApiKey = 'abc';</script>",
            200,
        )

        self.assertEqual(profile, "q4_or_q4_like")
        self.assertIn("Q4", recommendation)
        self.assertTrue(should_try_q4_feed("https://investor.example.com/news/default.aspx", profile, ""))

    def test_q4_feed_creates_document_when_detail_page_is_blocked(self):
        def fetcher(url):
            if url == "https://investor.example.com/news/default.aspx":
                return FetchResult(
                    url=url,
                    final_url=url,
                    status_code=200,
                    content_type="text/html",
                    text="<html><script>var Q4ApiKey = 'abc';</script></html>",
                )
            if "GetPressReleaseYearList" in url:
                return FetchResult(
                    url=url,
                    final_url=url,
                    status_code=200,
                    content_type="application/json",
                    text=json.dumps({"GetPressReleaseYearListResult": [2021]}),
                )
            if "GetPressReleaseList" in url:
                body = " ".join(
                    [
                        "Apple reported quarterly earnings, revenue, guidance, demand, margin, cash flow, and risks.",
                        "Management discussed supply chain conditions and capital return for shareholders.",
                    ]
                    * 8
                )
                return FetchResult(
                    url=url,
                    final_url=url,
                    status_code=200,
                    content_type="application/json",
                    text=json.dumps(
                        {
                            "GetPressReleaseListResult": [
                                {
                                    "Headline": "Apple reports quarterly results",
                                    "PressReleaseDate": "10/28/2021 16:01:00",
                                    "Body": body,
                                    "LinkToDetailPage": "/news/news-details/2021/apple-reports/default.aspx",
                                }
                            ]
                        }
                    ),
                )
            if "apple-reports" in url:
                return FetchResult(
                    url=url,
                    final_url=url,
                    status_code=403,
                    content_type="text/html",
                    text="forbidden",
                    error="forbidden",
                )
            return FetchResult(url=url, final_url=url, status_code=404, content_type="text/html", text="")

        with tempfile.TemporaryDirectory() as tmp:
            metadata = Path(tmp) / "metadata.csv"
            metadata.write_text(
                "ticker,cik,official_name,company_name,common_name,sector,aliases,source_credibility\n"
                "AAPL,0000320193,Apple Inc.,Apple Inc.,Apple Inc.,Information Technology,Apple|AAPL,0.9\n",
                encoding="utf-8",
            )
            documents, detail_manifest, source_manifest, vendor_queue = discover_documents_from_sources(
                [
                    {
                        "ticker": "AAPL",
                        "company": "Apple",
                        "source_type": "press_releases_archive",
                        "url": "https://investor.example.com/news/default.aspx",
                        "crawler_grade": "browser_or_api_needed",
                    }
                ],
                metadata_path=metadata,
                config=DiscoveryConfig(start_year=2021, end_year=2021, min_body_words=40, sleep_seconds=0),
                fetcher=fetcher,
            )

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["published_at"], "2021-10-28T16:01:00Z")
        self.assertEqual(documents[0]["discovery_method"], "q4_press_release_feed")
        self.assertEqual(source_manifest[0]["q4_feed_candidates_seen"], 1)
        self.assertTrue(detail_manifest[0]["api_payload_url"])
        self.assertEqual(vendor_queue[0]["priority"], "low_already_has_generic_yield")

    def test_decode_prefers_utf8_when_declared_encoding_would_mojibake(self):
        decoded = decode_response_text("Apple\u2019s results".encode("utf-8"), "", "ISO-8859-1")

        self.assertEqual(decoded, "Apple\u2019s results")


if __name__ == "__main__":
    unittest.main()
