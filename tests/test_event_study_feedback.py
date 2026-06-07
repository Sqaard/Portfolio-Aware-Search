import unittest

from features.build_event_study_feedback import Panel, _direction_from_return, _direction_match


class EventStudyFeedbackTests(unittest.TestCase):
    def test_direction_labels_and_mixed_match(self):
        self.assertEqual(_direction_from_return(0.02, 0.005), "positive")
        self.assertEqual(_direction_from_return(-0.02, 0.005), "negative")
        self.assertEqual(_direction_from_return(0.001, 0.005), "neutral")
        self.assertEqual(_direction_match("mixed", "positive"), 1)
        self.assertEqual(_direction_match("mixed", "neutral"), 0)

    def test_panel_returns_and_abnormal_inputs(self):
        rows = [
            {"date": "2020-01-01", "tic": "AAA", "close": "100", "daily_return": "0.0"},
            {"date": "2020-01-02", "tic": "AAA", "close": "110", "daily_return": "0.1"},
            {"date": "2020-01-03", "tic": "AAA", "close": "121", "daily_return": "0.1"},
            {"date": "2020-01-01", "tic": "BBB", "close": "100", "daily_return": "0.0"},
            {"date": "2020-01-02", "tic": "BBB", "close": "100", "daily_return": "0.0"},
            {"date": "2020-01-03", "tic": "BBB", "close": "100", "daily_return": "0.0"},
        ]
        panel = Panel(rows)

        self.assertAlmostEqual(panel.ticker_return("AAA", "2020-01-02", 0, 1), 0.1)
        self.assertAlmostEqual(panel.market_return("2020-01-02", 0, 1), 0.05)


if __name__ == "__main__":
    unittest.main()
