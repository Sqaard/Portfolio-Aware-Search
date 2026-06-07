from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finportfolio_ir.favorites import sort_results_for_refresh, toggle_favorite_in_place
from finportfolio_ir.my_vibe import build_portfolio_impact_prompt, sort_posts_for_my_vibe


class FavoritesAndMyVibeTests(unittest.TestCase):
    def test_favorites_sort_first_by_site_name_on_refresh(self):
        results = [
            {"rank": 1, "title": "Market", "url": "https://news.example.com/a", "site_name": "News", "score": 3.0},
            {"rank": 2, "title": "SEC filing", "url": "https://www.sec.gov/doc", "site_name": "SEC", "score": 2.8},
            {"rank": 3, "title": "Company post", "url": "https://ir.example.com/post", "site_name": "Company IR", "score": 2.7},
        ]

        sorted_results = sort_results_for_refresh(results, ["https://www.sec.gov/", "https://ir.example.com/"])

        self.assertEqual([row["site_name"] for row in sorted_results[:2]], ["Company IR", "SEC"])
        self.assertTrue(sorted_results[0]["favorite_highlight"])
        self.assertEqual(sorted_results[0]["favorite_icon"], "filled")

    def test_weak_favorite_does_not_beat_relevant_results(self):
        results = [
            {"rank": 1, "title": "Microsoft risk", "url": "https://www.sec.gov/msft", "site_name": "SEC", "score": 8.0},
            {"rank": 2, "title": "Apple unrelated", "url": "https://apple.com/iphone", "site_name": "Apple", "score": 1.0},
        ]

        sorted_results = sort_results_for_refresh(results, ["https://apple.com/"])

        self.assertEqual(sorted_results[0]["title"], "Microsoft risk")
        self.assertTrue(sorted_results[1]["favorite_highlight"])

    def test_toggle_favorite_does_not_reorder_until_refresh(self):
        results = [
            {"rank": 1, "title": "Market", "url": "https://news.example.com/a", "site_name": "News"},
            {"rank": 2, "title": "SEC filing", "url": "https://www.sec.gov/doc", "site_name": "SEC"},
        ]

        favorites, in_place = toggle_favorite_in_place(results, ["https://www.sec.gov/"], "https://www.sec.gov/doc")

        self.assertEqual(favorites, [])
        self.assertEqual([row["title"] for row in in_place], ["Market", "SEC filing"])
        self.assertEqual(in_place[1]["favorite_status"], "pending_removed")
        self.assertEqual(in_place[1]["favorite_icon"], "empty")

        favorites, in_place = toggle_favorite_in_place(results, favorites, "https://news.example.com/a")
        self.assertEqual(favorites, ["news.example.com"])
        self.assertEqual([row["title"] for row in in_place], ["Market", "SEC filing"])
        self.assertEqual(in_place[0]["favorite_status"], "pending_added")
        self.assertEqual(in_place[0]["favorite_icon"], "filled")
        self.assertTrue(in_place[0]["favorite_highlight"])

    def test_my_vibe_sorts_posts_and_hides_full_text_in_ui(self):
        portfolio = {"holdings": [{"ticker": "AAPL", "weight": 0.4, "sector": "Technology"}]}
        posts = [
            {"id": "m", "title": "General rates note", "text": "Rates and inflation.", "published_at": "2"},
            {"id": "a", "title": "Apple margin pressure", "text": "Apple earnings margin and demand.", "published_at": "1"},
        ]

        sorted_posts = sort_posts_for_my_vibe(posts, portfolio)
        prompt = build_portfolio_impact_prompt(posts[1], portfolio)

        self.assertEqual(sorted_posts[0]["id"], "a")
        self.assertIn("text_char_count", sorted_posts[0])
        self.assertNotIn("text", sorted_posts[0])
        self.assertIn("Apple earnings", prompt["post"]["text"])


if __name__ == "__main__":
    unittest.main()
