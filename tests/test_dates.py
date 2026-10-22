from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_package

load_package()

from maibot_wardrobe.calendar_rules import active_events, date_matches, is_valid_date_expression  # noqa: E402
from maibot_wardrobe.wardrobe import WardrobeEvent  # noqa: E402


class DateTests(unittest.TestCase):
    def test_annual(self) -> None:
        self.assertTrue(date_matches("10-28", date(2026, 10, 28)))
        self.assertTrue(date_matches("10-28", date(2027, 10, 28)))
        self.assertFalse(date_matches("10-28", date(2026, 10, 27)))

    def test_absolute(self) -> None:
        self.assertTrue(date_matches("2026-10-28", date(2026, 10, 28)))
        self.assertFalse(date_matches("2026-10-28", date(2027, 10, 28)))

    def test_annual_range_cross_year(self) -> None:
        self.assertTrue(date_matches("12-30..01-02", date(2026, 12, 31)))
        self.assertTrue(date_matches("12-30..01-02", date(2027, 1, 2)))
        self.assertFalse(date_matches("12-30..01-02", date(2027, 1, 3)))

    def test_absolute_range(self) -> None:
        self.assertTrue(date_matches("2026-12-24..2026-12-26", date(2026, 12, 25)))
        self.assertFalse(date_matches("2026-12-24..2026-12-26", date(2025, 12, 25)))

    def test_separator_variants(self) -> None:
        self.assertTrue(date_matches("12-24~12-26", date(2026, 12, 25)))
        self.assertTrue(date_matches("12-24 至 12-26", date(2026, 12, 25)))

    def test_invalid(self) -> None:
        self.assertFalse(date_matches("", date(2026, 1, 1)))
        self.assertFalse(date_matches("hello", date(2026, 1, 1)))
        self.assertFalse(is_valid_date_expression("hello"))
        self.assertTrue(is_valid_date_expression("10-28"))
        self.assertTrue(is_valid_date_expression("2026-10-28"))
        self.assertTrue(is_valid_date_expression("12-24..12-26"))
        self.assertTrue(is_valid_date_expression("2026-12-24..2026-12-26"))

    def test_active_events(self) -> None:
        events = [
            WardrobeEvent(id="a", name="生日", date="10-28"),
            WardrobeEvent(id="b", name="圣诞", date="12-24..12-26"),
            WardrobeEvent(id="c", name="关闭", date="10-28", enabled=False),
        ]
        matched = active_events(events, date(2026, 10, 28))
        self.assertEqual([event.id for event in matched], ["a"])


if __name__ == "__main__":
    unittest.main()
