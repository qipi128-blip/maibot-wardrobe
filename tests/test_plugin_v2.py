from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_package

load_package()

from fakes import PNG_BYTES, FakeContext  # noqa: E402
from maibot_wardrobe.plugin import WardrobePlugin  # noqa: E402


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._temp.name)
        self.ctx = FakeContext(self.data_dir)
        self.plugin = WardrobePlugin()
        self.plugin._set_context(self.ctx)
        config = self.plugin.build_default_config()
        config["vision"]["enabled"] = False
        self.plugin.set_plugin_config(config)
        await self.plugin.on_load()

    async def asyncTearDown(self) -> None:
        await self.plugin.on_unload()
        self._temp.cleanup()

    def test_components(self) -> None:
        components = {component["name"]: component for component in self.plugin.get_components()}
        self.assertIn("wardrobe_status", components)
        self.assertIn("wardrobe_planner_inject", components)
        self.assertIn("wardrobe_replyer_inject", components)
        self.assertIn("wardrobe_web_upload", components)
        self.assertEqual(components["wardrobe_web_upload"]["type"], "HOME_CARD")

    async def test_injection(self) -> None:
        items = [
            {
                "item_type": "SystemMessageItem",
                "meta": {"item_id": "sys", "logical_turn_id": None, "timestamp": "2026-10-22T12:00:00"},
                "parts": [{"type": "text", "text": "你是一个聊天机器人。"}],
            }
        ]
        result = await self.plugin.on_replyer_before_model_request(items=items, item_schema_version=1)
        self.assertIn("modified_kwargs", result)
        injected = items[0]["parts"][-1]["text"]
        self.assertIn("【你此刻的形象", injected)
        self.assertIn("大肥鱼", injected)
        self.assertNotIn("季节", injected)
        second = await self.plugin.on_replyer_before_model_request(items=items, item_schema_version=1)
        self.assertNotIn("modified_kwargs", second)

    async def test_status_command(self) -> None:
        await self.plugin.cmd_status(stream_id="stream-1")
        text = self.ctx.send.texts[-1][1]
        self.assertIn("今日穿搭", text)
        self.assertIn("衣橱：8 套", text)
        self.assertNotIn("季节", text)

    async def test_save_and_delete_outfit(self) -> None:
        result = await self.plugin._save_outfit_images("gone_look", "要删的", "临时", [PNG_BYTES])
        self.assertTrue(result["ok"])
        outfit_dir = self.data_dir / "outfits" / "gone_look"
        self.assertTrue(outfit_dir.is_dir())
        deleted = self.plugin._delete_outfit("gone_look")
        self.assertTrue(deleted["ok"])
        self.assertIsNone(self.plugin._store.wardrobe.get_outfit("gone_look"))  # type: ignore[union-attr]
        self.assertFalse(outfit_dir.exists())

    async def test_save_and_delete_image(self) -> None:
        result = await self.plugin._save_outfit_images("new_look", "新衣服", "一条测试裙子", [PNG_BYTES])
        self.assertTrue(result["ok"])
        outfit = self.plugin._store.wardrobe.get_outfit("new_look")  # type: ignore[union-attr]
        self.assertIsNotNone(outfit)
        self.assertEqual(len(outfit.images), 1)  # type: ignore[union-attr]
        path = self.data_dir / outfit.images[0]  # type: ignore[union-attr]
        self.assertTrue(path.is_file())

        deleted = self.plugin._delete_outfit_image("new_look", 0)
        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["image_count"], 0)
        self.assertFalse(path.exists())

    def test_wear_today(self) -> None:
        selection = self.plugin._wear_today("spring_breeze")
        self.assertIsNotNone(selection)
        self.assertEqual(selection.outfit_id, "spring_breeze")  # type: ignore[union-attr]
        self.assertEqual(self.plugin._state.today.outfit_id, "spring_breeze")  # type: ignore[union-attr]

    def test_event_save_and_delete(self) -> None:
        result = self.plugin._save_event("", "生日", "10-28", ["birthday_dress"], "今天穿了白色连衣裙")
        self.assertTrue(result["ok"])
        event = result["event"]
        self.assertEqual(len(self.plugin._store.wardrobe.events), 1)  # type: ignore[union-attr]
        deleted = self.plugin._delete_event(event["id"])
        self.assertTrue(deleted["ok"])
        self.assertEqual(len(self.plugin._store.wardrobe.events), 0)  # type: ignore[union-attr]

    def test_event_validation(self) -> None:
        self.assertFalse(self.plugin._save_event("", "", "10-28", ["birthday_dress"], "")["ok"])
        self.assertFalse(self.plugin._save_event("", "生日", "hello", ["birthday_dress"], "")["ok"])
        self.assertFalse(self.plugin._save_event("", "生日", "10-28", [], "")["ok"])
        self.assertFalse(self.plugin._save_event("", "生日", "10-28", ["no_such"], "")["ok"])


if __name__ == "__main__":
    unittest.main()
