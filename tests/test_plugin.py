from __future__ import annotations

import base64
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
        config["upload"]["allow_all"] = True
        config["vision"]["enabled"] = False
        config["debug"]["simulated_date"] = "2026-10-22"
        self.plugin.set_plugin_config(config)
        await self.plugin.on_load()

    async def asyncTearDown(self) -> None:
        await self.plugin.on_unload()
        self._temp.cleanup()

    def test_components_registered(self) -> None:
        components = {component["name"]: component for component in self.plugin.get_components()}
        for name in (
            "wardrobe_status",
            "wardrobe_list",
            "wardrobe_help",
            "wardrobe_upload",
            "wardrobe_describe",
            "wardrobe_reroll",
            "wardrobe_wear",
            "wardrobe_events",
            "wardrobe_preview",
            "wardrobe_selfie",
            "wardrobe_send_selfie",
            "wardrobe_planner_inject",
            "wardrobe_replyer_inject",
        ):
            self.assertIn(name, components)
        self.assertEqual(components["wardrobe_send_selfie"]["type"], "TOOL")
        self.assertEqual(components["wardrobe_planner_inject"]["type"], "HOOK_HANDLER")

    async def test_replyer_injection(self) -> None:
        items = [
            {
                "item_type": "SystemMessageItem",
                "meta": {"item_id": "sys", "logical_turn_id": None, "timestamp": "2026-10-22T12:00:00"},
                "parts": [{"type": "text", "text": "你是一个聊天机器人。"}],
            }
        ]
        result = await self.plugin.on_replyer_before_model_request(
            items=items,
            item_schema_version=1,
            session_id="stream-1",
            selected_model_visual=False,
        )
        self.assertEqual(result["action"], "continue")
        self.assertIn("modified_kwargs", result)
        injected = items[0]["parts"][-1]["text"]
        self.assertIn("【你此刻的形象", injected)
        self.assertIn("大肥鱼", injected)
        self.assertIn("今天", injected)
        self.assertEqual(self.plugin._active_stream_id(), "stream-1")

        # 第二次不应重复注入
        second = await self.plugin.on_replyer_before_model_request(items=items, item_schema_version=1)
        self.assertNotIn("modified_kwargs", second)
        self.assertEqual(sum("【你此刻的形象" in part["text"] for part in items[0]["parts"] if part.get("type") == "text"), 1)

    async def test_injection_can_be_disabled(self) -> None:
        config = self.plugin.get_plugin_config_data()
        config["injection"]["enabled"] = False
        self.plugin.set_plugin_config(config)
        result = await self.plugin.on_planner_before_request(items=[{"item_type": "SystemMessageItem"}])
        self.assertNotIn("modified_kwargs", result)

    async def test_status_command(self) -> None:
        await self.plugin.cmd_status(
            stream_id="stream-1",
            user_id="10001",
            message={"message_id": "m1", "message_info": {"user_info": {"user_id": "10001"}}},
        )
        self.assertTrue(self.ctx.send.texts)
        text = self.ctx.send.texts[-1][1]
        self.assertIn("今日穿搭", text)
        self.assertIn("季节：秋天", text)
        self.assertIn("衣橱：8 套", text)

    async def test_birthday_selection_and_status(self) -> None:
        config = self.plugin.get_plugin_config_data()
        config["debug"]["simulated_date"] = "2026-10-28"
        self.plugin.set_plugin_config(config)
        selection = self.plugin._ensure_today(self.plugin._today())
        assert selection is not None
        self.assertEqual(selection.outfit_id, "birthday_dress")
        await self.plugin.cmd_status(stream_id="stream-1", user_id="1")
        text = self.ctx.send.texts[-1][1]
        self.assertIn("生日", text)
        self.assertIn("生日礼服", text)

    async def test_wear_command(self) -> None:
        ok, _message, _intercept = await self.plugin.cmd_wear(
            stream_id="stream-1",
            user_id="10001",
            is_local_operator=True,
            text="/衣橱穿 spring_breeze",
        )
        self.assertTrue(ok)
        selection = self.plugin._state.today  # type: ignore[union-attr]
        assert selection is not None
        self.assertEqual(selection.outfit_id, "spring_breeze")
        self.assertEqual(selection.source, "manual")
        self.assertIn("穿「春日微风」", self.ctx.send.texts[-1][1])

    async def test_upload_with_current_image(self) -> None:
        self.ctx.message.messages["m-upload"] = {
            "message_id": "m-upload",
            "raw_message": [
                {"type": "image", "data": "", "binary_data_base64": base64.b64encode(PNG_BYTES).decode()}
            ],
            "message_info": {"user_info": {"user_id": "10001"}},
        }
        ok, _message, _intercept = await self.plugin.cmd_upload(
            stream_id="stream-1",
            user_id="10001",
            text="/衣橱上传 test_look 一条测试裙子",
            message={"message_id": "m-upload", "message_info": {"user_info": {"user_id": "10001"}}},
        )
        self.assertTrue(ok)
        wardrobe = self.plugin._store.wardrobe  # type: ignore[union-attr]
        outfit = wardrobe.get_outfit("test_look")
        self.assertIsNotNone(outfit)
        self.assertEqual(len(outfit.images), 1)  # type: ignore[union-attr]
        self.assertTrue((self.data_dir / outfit.images[0]).is_file())  # type: ignore[union-attr]
        self.assertIn("新建", self.ctx.send.texts[-1][1])

    async def test_upload_permission_denied(self) -> None:
        config = self.plugin.get_plugin_config_data()
        config["upload"]["allow_all"] = False
        config["upload"]["allowed_user_ids"] = []
        config["commands"]["admin_user_ids"] = []
        self.plugin.set_plugin_config(config)
        self.ctx.message.messages["m-upload"] = {
            "message_id": "m-upload",
            "raw_message": [
                {"type": "image", "data": "", "binary_data_base64": base64.b64encode(PNG_BYTES).decode()}
            ],
        }
        await self.plugin.cmd_upload(
            stream_id="stream-1",
            user_id="20002",
            is_local_operator=False,
            text="/衣橱上传 nope 不行",
            message={"message_id": "m-upload", "message_info": {"user_info": {"user_id": "20002"}}},
        )
        self.assertIn("权限", self.ctx.send.texts[-1][1])
        self.assertIsNone(self.plugin._store.wardrobe.get_outfit("nope"))  # type: ignore[union-attr]

    async def test_selfie_tool_sends_image(self) -> None:
        selection = self.plugin._state.today  # type: ignore[union-attr]
        assert selection is not None
        outfit = self.plugin._store.wardrobe.get_outfit(selection.outfit_id)  # type: ignore[union-attr]
        assert outfit is not None
        target = self.data_dir / "outfits" / outfit.id / "test.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PNG_BYTES)
        outfit.images.append(f"outfits/{outfit.id}/test.png")
        self.plugin._store.save()  # type: ignore[union-attr]
        self.plugin._remember_session("stream-1")

        result = await self.plugin.tool_send_selfie()
        self.assertIn("照片", result["content"])
        self.assertEqual(len(self.ctx.send.images), 1)
        stream_id, image_base64 = self.ctx.send.images[0]
        self.assertEqual(stream_id, "stream-1")
        self.assertTrue(base64.b64decode(image_base64).startswith(b"\x89PNG"))

    async def test_describe_command(self) -> None:
        config = self.plugin.get_plugin_config_data()
        config["vision"]["enabled"] = True
        config["commands"]["admin_user_ids"] = ["10001"]
        self.plugin.set_plugin_config(config)
        selection = self.plugin._state.today  # type: ignore[union-attr]
        assert selection is not None
        outfit = self.plugin._store.wardrobe.get_outfit(selection.outfit_id)  # type: ignore[union-attr]
        assert outfit is not None
        target = self.data_dir / "outfits" / outfit.id / "test.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PNG_BYTES)
        outfit.images.append(f"outfits/{outfit.id}/test.png")
        self.plugin._store.save()  # type: ignore[union-attr]

        await self.plugin.cmd_describe(
            stream_id="stream-1",
            user_id="10001",
            text=f"/衣橱描述 {outfit.id}",
        )
        self.assertEqual(outfit.vision_description, "白色连衣裙，黑色长发。")
        self.assertEqual(len(self.ctx.llm.calls), 1)
        self.assertEqual(self.ctx.llm.calls[0]["task_name"], "vlm")

    async def test_events_command(self) -> None:
        await self.plugin.cmd_events(stream_id="stream-1", user_id="1")
        text = self.ctx.send.texts[-1][1]
        self.assertIn("特殊日子", text)
        self.assertIn("生日", text)

    async def test_help_command(self) -> None:
        await self.plugin.cmd_help(stream_id="stream-1", user_id="1")
        self.assertIn("/衣橱上传", self.ctx.send.texts[-1][1])

    def test_command_patterns(self) -> None:
        import re

        patterns = {
            component["name"]: component["metadata"]["command_pattern"]
            for component in self.plugin.get_components()
            if component["type"] == "COMMAND"
        }
        cases = {
            "/衣橱": "wardrobe_status",
            "/衣橱状态": "wardrobe_status",
            "/衣橱列表": "wardrobe_list",
            "/衣橱帮助": "wardrobe_help",
            "/衣橱上传 spring_dress 春日白裙": "wardrobe_upload",
            "/衣橱描述 spring_dress": "wardrobe_describe",
            "/衣橱换装": "wardrobe_reroll",
            "/衣橱穿 spring_breeze": "wardrobe_wear",
            "/衣橱事件": "wardrobe_events",
            "/衣橱预览 2026-10-28": "wardrobe_preview",
            "/看看你": "wardrobe_selfie",
            "/自拍": "wardrobe_selfie",
        }
        for text, expected in cases.items():
            matched = [name for name, pattern in patterns.items() if re.search(pattern, text)]
            self.assertEqual(matched, [expected], text)


if __name__ == "__main__":
    unittest.main()
