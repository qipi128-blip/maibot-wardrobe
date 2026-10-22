from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_package

load_package()

from maibot_wardrobe.wardrobe import Wardrobe, WardrobeStore, safe_outfit_id  # noqa: E402


class WardrobeStoreTests(unittest.TestCase):
    def test_initialize_from_template_and_reload(self) -> None:
        package_dir = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "wardrobe.toml"
            store = WardrobeStore(path, template_path=package_dir / "assets" / "default_wardrobe.toml")
            wardrobe = store.ensure_loaded()
            self.assertEqual(len(wardrobe.outfits), 8)
            self.assertTrue(path.is_file())

            outfit, created = wardrobe.ensure_outfit("test_look", name="测试套装", description="一条测试裙子")
            self.assertTrue(created)
            store.save(wardrobe)

            reloaded = WardrobeStore(path).ensure_loaded(force=True)
            self.assertIsNotNone(reloaded.get_outfit("test_look"))
            self.assertEqual(reloaded.get_outfit("test_look").description, "一条测试裙子")  # type: ignore[union-attr]

    def test_invalid_toml_keeps_last_good_data(self) -> None:
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

    def test_safe_outfit_id(self) -> None:
        self.assertEqual(safe_outfit_id("spring_dress"), "spring_dress")
        for bad in ["", "中文", "../x", "a b", "a/b"]:
            with self.assertRaises(ValueError):
                safe_outfit_id(bad)


class WardrobeParsingTests(unittest.TestCase):
    def test_duplicate_ids_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Wardrobe.from_mapping(
                {
                    "outfits": [
                        {"id": "same", "name": "一"},
                        {"id": "same", "name": "二"},
                    ]
                }
            )

    def test_invalid_images_ignored(self) -> None:
        wardrobe = Wardrobe.from_mapping(
            {"outfits": [{"id": "x", "images": ["../secret.png", "outfits/x/a.png", "outfits/x/a.png"]}]}
        )
        self.assertEqual(wardrobe.outfits[0].images, ["outfits/x/a.png"])


if __name__ == "__main__":
    unittest.main()
