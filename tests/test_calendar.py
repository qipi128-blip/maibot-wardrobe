from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_package

load_package()

from maibot_wardrobe.calendar_rules import (  # noqa: E402
    active_events,
    event_matches,
    has_lunar_support,
    season_of,
    upcoming_events,
)
from maibot_wardrobe.wardrobe import WardrobeEvent  # noqa: E402


class SeasonTests(unittest.TestCase):
    def test_northern_seasons(self) -> None:
        self.assertEqual(season_of(date(2026, 4, 1)), "spring")
        self.assertEqual(season_of(date(2026, 7, 1)), "summer")
        self.assertEqual(season_of(date(2026, 10, 22)), "autumn")
        self.assertEqual(season_of(date(2026, 12, 1)), "winter")
        self.assertEqual(season_of(date(2026, 1, 1)), "winter")

    def test_southern_seasons(self) -> None:
        self.assertEqual(season_of(date(2026, 1, 1), hemisphere="southern"), "summer")
        self.assertEqual(season_of(date(2026, 7, 1), hemisphere="southern"), "winter")

    def test_custom_ranges_override(self) -> None:
        ranges = {"spring": "02-01..04-15", "summer": "04-16..06-30"}
        self.assertEqual(season_of(date(2026, 2, 10), ranges=ranges), "spring")
        self.assertEqual(season_of(date(2026, 4, 16), ranges=ranges), "summer")


class EventTests(unittest.TestCase):
    def test_annual_date(self) -> None:
        event = WardrobeEvent(id="birthday", dates=["10-28"])
        self.assertTrue(event_matches(event, date(2026, 10, 28), "autumn"))
        self.assertFalse(event_matches(event, date(2026, 10, 27), "autumn"))

    def test_absolute_date(self) -> None:
        event = WardrobeEvent(id="once", dates=["2026-10-28"])
        self.assertTrue(event_matches(event, date(2026, 10, 28), "autumn"))
        self.assertFalse(event_matches(event, date(2027, 10, 28), "autumn"))

    def test_cross_year_range(self) -> None:
        event = WardrobeEvent(id="newyear", ranges=["12-30..01-02"])
        self.assertTrue(event_matches(event, date(2026, 12, 31), "winter"))
        self.assertTrue(event_matches(event, date(2027, 1, 2), "winter"))
        self.assertFalse(event_matches(event, date(2027, 1, 3), "winter"))

    def test_weekday_and_season_filters(self) -> None:
        event = WardrobeEvent(id="friday", weekdays=[5], seasons=["autumn"])
        self.assertTrue(event_matches(event, date(2026, 10, 23), "autumn"))  # 周五
        self.assertFalse(event_matches(event, date(2026, 10, 24), "autumn"))  # 周六
        self.assertFalse(event_matches(event, date(2026, 7, 3), "summer"))  # 周五但夏天

    def test_priority_order(self) -> None:
        events = [
            WardrobeEvent(id="low", priority=10, dates=["10-28"]),
            WardrobeEvent(id="high", priority=200, dates=["10-28"]),
        ]
        matched = active_events(events, date(2026, 10, 28), "autumn")
        self.assertEqual([event.id for event in matched], ["high", "low"])

    @unittest.skipUnless(has_lunar_support(), "需要可选的 lunardate 依赖")
    def test_lunar_new_year(self) -> None:
        event = WardrobeEvent(id="spring", lunar_dates=["01-01"])
        # 2026 年春节是公历 2026-02-17
        self.assertTrue(event_matches(event, date(2026, 2, 17), "winter"))
        self.assertFalse(event_matches(event, date(2026, 2, 18), "winter"))

    def test_upcoming_events_deduplicate(self) -> None:
        events = [WardrobeEvent(id="birthday", dates=["10-28"])]
        upcoming = upcoming_events(events, date(2026, 10, 20), 30)
        self.assertEqual(len(upcoming), 1)
        self.assertEqual(upcoming[0][0], date(2026, 10, 28))


if __name__ == "__main__":
    unittest.main()
