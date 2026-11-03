"""
Tests for the traffic pipeline's resilience logic.

Covers:
  - fetch_analytics._incomplete_dates: partial days are re-queued, normal listing growth is not
  - sync_listings._validate_active_count: refuses to mark everything ended on a bad response
  - compute_metrics.compute: recompute window is bounded unless full=True

All DB and network calls are mocked — no live credentials needed.
"""

import os
import sys
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from traffic_analytics import compute_metrics, fetch_analytics, sync_listings


def _counts(start: date, rows: list[int]) -> dict[date, int]:
    return {start + timedelta(days=i): n for i, n in enumerate(rows)}


class TestIncompleteDates(unittest.TestCase):
    start = date(2026, 8, 1)

    def test_partial_day_is_flagged(self):
        # 2026-08-06 stored 91 of ~265 rows after a rate-limited run.
        counts = _counts(self.start, [265, 265, 265, 265, 265, 91, 265, 266])
        result = fetch_analytics._incomplete_dates(counts, self.start, self.start + timedelta(days=7), limit=5)
        self.assertEqual(result, [date(2026, 8, 6)])

    def test_missing_day_is_flagged(self):
        counts = _counts(self.start, [265, 0, 265])
        result = fetch_analytics._incomplete_dates(counts, self.start, self.start + timedelta(days=2), limit=5)
        self.assertEqual(result, [date(2026, 8, 2)])

    def test_normal_listing_growth_is_not_flagged(self):
        # ~5% day-over-day growth from new listings is not a gap.
        counts = _counts(self.start, [541, 541, 571, 571, 577, 581, 610, 615])
        result = fetch_analytics._incomplete_dates(counts, self.start, self.start + timedelta(days=7), limit=5)
        self.assertEqual(result, [])

    def test_newest_first_and_limited(self):
        counts = _counts(self.start, [0, 0, 0, 0, 0, 0])
        result = fetch_analytics._incomplete_dates(counts, self.start, self.start + timedelta(days=5), limit=3)
        self.assertEqual(result, [date(2026, 8, 6), date(2026, 8, 5), date(2026, 8, 4)])

    def test_window_edge_uses_the_neighbor_outside_the_window(self):
        # Last day of the window has only 91 rows; its neighbor is yesterday (outside the window).
        counts = _counts(self.start, [265, 265, 91, 265])
        result = fetch_analytics._incomplete_dates(counts, self.start, self.start + timedelta(days=2), limit=5)
        self.assertEqual(result, [date(2026, 8, 3)])

    def test_get_missing_dates_queries_counts_with_neighbors(self):
        cur = MagicMock()
        cur.fetchall.return_value = [(date(2026, 8, 2), 100), (date(2026, 8, 3), 5)]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        with patch.object(fetch_analytics, "get_connection", return_value=conn):
            result = fetch_analytics._get_missing_dates(date(2026, 8, 2), date(2026, 8, 3), limit=5)
        self.assertEqual(result, [date(2026, 8, 3)])
        sql, params = cur.execute.call_args[0]
        self.assertIn("GROUP BY date", sql)
        self.assertEqual(params, (date(2026, 8, 1), date(2026, 8, 4)))


class TestValidateActiveCount(unittest.TestCase):
    def test_zero_fetched_with_stored_listings_raises(self):
        with self.assertRaises(RuntimeError):
            sync_listings._validate_active_count(stored_active=640, fetched=0)

    def test_large_drop_raises(self):
        with self.assertRaises(RuntimeError):
            sync_listings._validate_active_count(stored_active=640, fetched=200)

    def test_normal_churn_passes(self):
        sync_listings._validate_active_count(stored_active=640, fetched=610)
        sync_listings._validate_active_count(stored_active=640, fetched=700)

    def test_empty_database_passes(self):
        sync_listings._validate_active_count(stored_active=0, fetched=0)
        sync_listings._validate_active_count(stored_active=0, fetched=300)

    def test_small_catalog_is_not_guarded_against_percentage_drops(self):
        sync_listings._validate_active_count(stored_active=10, fetched=3)


class TestComputeMetricsWindow(unittest.TestCase):
    def _run(self, **kwargs):
        cur = MagicMock()
        cur.rowcount = 0
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        with patch.object(compute_metrics, "get_connection", return_value=conn):
            compute_metrics.compute(**kwargs)
        return cur.execute.call_args[0]

    def test_default_recomputes_only_recent_days(self):
        sql, params = self._run()
        expected = date.today() - timedelta(days=compute_metrics.LOOKBACK_DAYS)
        self.assertEqual(params, (expected, expected))
        self.assertIn("WHERE r.date >= %s", sql)
        self.assertIn("WHERE order_date >= %s", sql)

    def test_full_recomputes_everything(self):
        _, params = self._run(full=True)
        self.assertEqual(params, (date.min, date.min))


if __name__ == "__main__":
    unittest.main()
