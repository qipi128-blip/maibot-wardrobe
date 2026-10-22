from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_package

load_package()

from maibot_wardrobe.wardrobe import Wardrobe, WardrobeStore, safe_outfit_id  # noqa: E402


class DataTests(unittest.TestCase):
    def test_template_has_no_events(self) -> None:
        package_dir = Path(__file__).resolve().parents[1]
        wardrobe = Wardrobe.from_toml((package_dir / "assets" / "default_wardrobe.toml").read_text(encoding="utf-8"))
        self.assertEqual(len(wardrobe.outfits), 8)
        self.assertEqual(len(wardrobe.events), 0)

    def test_store_roundtrip(self) -> None:
        package_dir = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "wardrobe.toml"
            store = WardrobeStore(path, template_path=package_dir / "assets" / "default_wardrobe.toml")
            wardrobe = store.ensure_loaded()
            outfit, created = wardrobe.ensure_outfit("test_look", name="测试", description="一条裙子")
            self.assertTrue(created)
            store.save(wardrobe)
            reloaded = WardrobeStore(path).ensure_loaded(force=True)
            self.assertEqual(reloaded.get_outfit("test_look").description, "一条裙子")  # type: ignore[union-attr]

    def test_invalid_toml_keeps_last_good(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "wardrobe.toml"
            path.write_text('[[outfits]]\nid = "ok"\nname = "好的"\n', encoding="utf-8")
            store = WardrobeStore(path)
            store.ensure_loaded()
            self.assertIsNotNone(store.wardrobe.get_outfit("ok"))
            path.write_text("[[outfits]\nbroken", encoding="utf-8")
            store.ensure_loaded()
            self.assertIsNotNone(store.wardrobe.get_outfit("ok"))
            self.assertTrue(store.last_error)

    def test_migrates_old_event_fields(self) -> None:
        wardrobe = Wardrobe.from_mapping(
            {
                "outfits": [{"id": "dress"}],
                "events": [{"id": "b", "name": "生日", "dates": ["10-28"], "outfits": ["dress"]}],
            }
        )
        self.assertEqual(wardrobe.events[0].date, "10-28")

    def test_old_outfit_fields_ignored(self) -> None:
        wardrobe = Wardrobe.from_mapping(
            {
                "outfits": [
                    {"id": "dress", "name": "裙子", "seasons": ["spring"], "weight": 2.0, "tags": ["x"]}
                ]
            }
        )
        self.assertEqual(wardrobe.outfits[0].name, "裙子")

    def test_safe_id(self) -> None:
        self.assertEqual(safe_outfit_id("spring_dress"), "spring_dress")
        for bad in ["", "中文", "../x", "a b", "a/b"]:
            with self.assertRaises(ValueError):
                safe_outfit_id(bad)

    def test_duplicate_ids(self) -> None:
        with self.assertRaises(ValueError):
            Wardrobe.from_mapping({"outfits": [{"id": "x"}, {"id": "x"}]})


if __name__ == "__main__":
    unittest.main()
