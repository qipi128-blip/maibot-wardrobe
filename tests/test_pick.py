from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_package

load_package()

from maibot_wardrobe.wardrobe import Wardrobe, select_outfit  # noqa: E402


def make_wardrobe() -> Wardrobe:
    return Wardrobe.from_mapping(
        {
            "outfits": [
                {"id": "daily", "name": "日常"},
                {"id": "coat", "name": "外套"},
                {"id": "dress", "name": "礼服"},
            ],
            "events": [
                {
                    "id": "birthday",
                    "name": "生日",
                    "date": "10-28",
                    "outfits": ["dress"],
                    "note": "今天穿了白色连衣裙。",
                }
            ],
        }
    )


class PickTests(unittest.TestCase):
    def test_event_overrides_daily(self) -> None:
        selection = select_outfit(make_wardrobe(), date(2026, 10, 28))
        assert selection is not None
        self.assertEqual(selection.outfit_id, "dress")
        self.assertEqual(selection.source, "event")
        self.assertIn("birthday", selection.event_ids)
        self.assertIn("白色连衣裙", selection.note)

    def test_normal_day_random(self) -> None:
        selection = select_outfit(make_wardrobe(), date(2026, 10, 22))
        assert selection is not None
        self.assertIn(selection.outfit_id, {"daily", "coat", "dress"})
        self.assertEqual(selection.source, "daily")

    def test_same_day_stable(self) -> None:
        first = select_outfit(make_wardrobe(), date(2026, 10, 22))
        second = select_outfit(make_wardrobe(), date(2026, 10, 22))
        self.assertEqual(first.outfit_id, second.outfit_id)  # type: ignore[union-attr]

    def test_avoid_repeat(self) -> None:
        selection = select_outfit(
            make_wardrobe(),
            date(2026, 10, 22),
            avoid_ids={"daily", "coat"},
            avoid_repeat_days=3,
        )
        assert selection is not None
        self.assertEqual(selection.outfit_id, "dress")

    def test_empty_wardrobe(self) -> None:
        self.assertIsNone(select_outfit(Wardrobe(), date(2026, 10, 22)))

    def test_event_without_outfits_falls_back(self) -> None:
        wardrobe = Wardrobe.from_mapping(
            {
                "outfits": [{"id": "daily"}],
                "events": [{"id": "note_only", "name": "考试周", "date": "10-22", "note": "最近在考试"}],
            }
        )
        selection = select_outfit(wardrobe, date(2026, 10, 22))
        assert selection is not None
        self.assertEqual(selection.outfit_id, "daily")
        self.assertIn("考试", selection.note)
        self.assertEqual(selection.source, "daily")


if __name__ == "__main__":
    unittest.main()
