from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_package

load_package()

from maibot_wardrobe.wardrobe import (  # noqa: E402
    Wardrobe,
    WardrobeEvent,
    select_outfit,
)


def make_wardrobe() -> Wardrobe:
    return Wardrobe.from_mapping(
        {
            "outfits": [
                {"id": "spring", "name": "春装", "seasons": ["spring"], "weight": 1},
                {"id": "autumn", "name": "秋装", "seasons": ["autumn"], "weight": 1},
                {"id": "all", "name": "四季", "seasons": [], "weight": 1},
                {"id": "event_only", "name": "礼服", "seasons": [], "only_for_events": True},
            ],
            "events": [
                {
                    "id": "birthday",
                    "name": "生日",
                    "priority": 200,
                    "dates": ["10-28"],
                    "outfits": ["event_only"],
                }
            ],
        }
    )


class SelectionTests(unittest.TestCase):
    def test_event_overrides_rotation(self) -> None:
        selection = select_outfit(make_wardrobe(), date(2026, 10, 28), season_id="autumn")
        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.outfit_id, "event_only")
        self.assertEqual(selection.source, "event")
        self.assertIn("birthday", selection.event_ids)

    def test_event_only_outfit_not_in_daily_rotation(self) -> None:
        wardrobe = make_wardrobe()
        seen: set[str] = set()
        for offset in range(30):
            day = date(2026, 9, 1 + offset) if offset < 29 else date(2026, 10, 1)
            selection = select_outfit(wardrobe, day, season_id="autumn", avoid_repeat_days=0)
            self.assertIsNotNone(selection)
            seen.add(selection.outfit_id)  # type: ignore[union-attr]
        self.assertNotIn("event_only", seen)
        self.assertEqual(seen, {"autumn", "all"})

    def test_season_pool(self) -> None:
        selection = select_outfit(make_wardrobe(), date(2026, 4, 1), season_id="spring")
        assert selection is not None
        self.assertIn(selection.outfit_id, {"spring", "all"})
        self.assertEqual(selection.source, "season")

    def test_daily_result_is_stable(self) -> None:
        wardrobe = make_wardrobe()
        first = select_outfit(wardrobe, date(2026, 10, 22), season_id="autumn")
        second = select_outfit(wardrobe, date(2026, 10, 22), season_id="autumn")
        self.assertEqual(first.outfit_id, second.outfit_id)  # type: ignore[union-attr]

    def test_avoid_repeat(self) -> None:
        selection = select_outfit(
            make_wardrobe(),
            date(2026, 10, 22),
            season_id="autumn",
            avoid_ids={"autumn"},
            avoid_repeat_days=7,
        )
        assert selection is not None
        self.assertEqual(selection.outfit_id, "all")

    def test_empty_wardrobe(self) -> None:
        self.assertIsNone(select_outfit(Wardrobe(), date(2026, 10, 22)))

    def test_rotation_disabled_is_stable(self) -> None:
        wardrobe = make_wardrobe()
        first = select_outfit(wardrobe, date(2026, 10, 22), season_id="autumn", rotation_enabled=False)
        second = select_outfit(wardrobe, date(2026, 10, 23), season_id="autumn", rotation_enabled=False)
        self.assertEqual(first.outfit_id, second.outfit_id)  # type: ignore[union-attr]

    def test_event_without_outfits_falls_back(self) -> None:
        wardrobe = Wardrobe.from_mapping(
            {
                "outfits": [{"id": "coat", "seasons": ["winter"]}],
                "events": [{"id": "cold", "priority": 10, "seasons": ["winter"], "note": "很冷"}],
            }
        )
        selection = select_outfit(wardrobe, date(2026, 1, 5), season_id="winter")
        assert selection is not None
        self.assertEqual(selection.outfit_id, "coat")
        self.assertEqual(selection.note, "很冷")


if __name__ == "__main__":
    unittest.main()
