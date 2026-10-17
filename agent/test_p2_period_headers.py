from __future__ import annotations

import unittest

from interim import header_period
from periods import report_period


class ExplicitDatePeriodTests(unittest.TestCase):
    def setUp(self):
        self.quarter = report_period("中国移动2026年第一季度报告", 2026)

    def test_flow_header_accepts_explicit_from_to_range(self):
        period = header_period(
            "由2026年1月1日至2026年3月31日期间",
            self.quarter,
            stock=False,
        )
        self.assertIsNotNone(period)
        self.assertEqual(period["period_year"], 2026)
        self.assertEqual(period["period_start"], "2026-01-01")
        self.assertEqual(period["period_end"], "2026-03-31")
        self.assertEqual(period["period_kind"], "quarter")
        self.assertEqual(period["duration_months"], 3)

    def test_stock_header_accepts_explicit_as_of_date(self):
        period = header_period("于2026年3月31日", self.quarter, stock=True)
        self.assertIsNotNone(period)
        self.assertEqual(period["period_year"], 2026)
        self.assertEqual(period["period_start"], "2026-03-31")
        self.assertEqual(period["period_end"], "2026-03-31")
        self.assertEqual(period["period_kind"], "instant")
        self.assertEqual(period["duration_months"], 0)


if __name__ == "__main__":
    unittest.main()
