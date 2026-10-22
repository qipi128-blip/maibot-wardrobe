from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_package

load_package()

from fakes import PNG_BYTES, FakeContext  # noqa: E402
from maibot_wardrobe.plugin import WardrobePlugin  # noqa: E402


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._temp.name)
        self.ctx = FakeContext(self.data_dir)
        self.plugin = WardrobePlugin()
        self.plugin._set_context(self.ctx)
        config = self.plugin.build_default_config()
        config["vision"]["enabled"] = False
        config["web_ui"]["port"] = 0
        self.plugin.set_plugin_config(config)
        await self.plugin.on_load()

    async def asyncTearDown(self) -> None:
        await self.plugin.on_unload()
        self._temp.cleanup()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.plugin._web_port}"

    async def request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        token: str | None = None,
        content_type: str = "application/octet-stream",
    ) -> tuple[int, bytes, dict[str, str]]:
        actual_token = self.plugin._web_token if token is None else token
        url = self.base_url + path
        if actual_token:
            url += ("&" if "?" in path else "?") + "token=" + urllib.parse.quote(actual_token)
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": content_type} if data is not None else {},
            method=method,
        )

        def run() -> tuple[int, bytes, dict[str, str]]:
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.status, response.read(), dict(response.headers)
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read(), dict(exc.headers)

        return await asyncio.to_thread(run)

    async def test_home_card(self) -> None:
        components = {component["name"]: component for component in self.plugin.get_components()}
        card = components["wardrobe_web_upload"]
        self.assertIn(f"127.0.0.1:{self.plugin._web_port}", card["metadata"]["link_url"])

    async def test_state_and_page(self) -> None:
        status, body, _headers = await self.request("/api/state")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["name"], "大肥鱼")
        self.assertEqual(payload["date"], self.plugin._today().isoformat())
        self.assertEqual(len(payload["outfits"]), 8)
        self.assertEqual(payload["events"], [])
        self.assertNotIn("season", payload["today"])
        self.assertEqual(payload["max_file_mb"], 16.0)

        status, body, _headers = await self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        self.assertIn("特殊事件", page)
        self.assertIn("删除", page)
        self.assertIn("换一套", page)
        self.assertNotIn("春装", page)

    async def test_token_required(self) -> None:
        status, _body, _headers = await self.request("/api/state", token="")
        self.assertEqual(status, 403)

    async def test_upload_and_delete_image(self) -> None:
        query = "/api/upload?outfit_id=web_test&name=" + urllib.parse.quote("网页测试")
        status, body, _headers = await self.request(query, method="POST", data=PNG_BYTES, content_type="image/png")
        self.assertEqual(status, 200, body[:300])
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        outfit = self.plugin._store.wardrobe.get_outfit("web_test")  # type: ignore[union-attr]
        self.assertIsNotNone(outfit)
        self.assertEqual(len(outfit.images), 1)  # type: ignore[union-attr]
        image_path = self.data_dir / outfit.images[0]  # type: ignore[union-attr]
        self.assertTrue(image_path.is_file())

        status, body, _headers = await self.request("/api/delete_image?outfit_id=web_test&index=0", method="POST")
        self.assertEqual(status, 200, body[:300])
        self.assertTrue(json.loads(body)["ok"])
        self.assertFalse(image_path.exists())

    async def test_event_save_delete(self) -> None:
        query = (
            "/api/event/save?name=" + urllib.parse.quote("生日")
            + "&date=" + urllib.parse.quote("10-28")
            + "&outfits=birthday_dress"
            + "&note=" + urllib.parse.quote("今天是生日")
        )
        status, body, _headers = await self.request(query, method="POST")
        self.assertEqual(status, 200, body[:300])
        self.assertTrue(json.loads(body)["ok"])

        status, body, _headers = await self.request("/api/state")
        events = json.loads(body)["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["date"], "10-28")

        status, body, _headers = await self.request("/api/event/delete?id=" + urllib.parse.quote(events[0]["id"]), method="POST")
        self.assertEqual(status, 200, body[:300])
        status, body, _headers = await self.request("/api/state")
        self.assertEqual(json.loads(body)["events"], [])

    async def test_select_and_reroll(self) -> None:
        status, body, _headers = await self.request("/api/select?outfit_id=spring_breeze", method="POST")
        self.assertEqual(status, 200, body[:300])
        status, body, _headers = await self.request("/api/state")
        self.assertEqual(json.loads(body)["today"]["outfit_id"], "spring_breeze")

        status, body, _headers = await self.request("/api/reroll", method="POST")
        self.assertEqual(status, 200, body[:300])

    async def test_oversized(self) -> None:
        config = self.plugin.get_plugin_config_data()
        config["upload"]["max_file_mb"] = 0.5
        self.plugin.set_plugin_config(config)
        status, body, _headers = await self.request(
            "/api/upload?outfit_id=too_big",
            method="POST",
            data=PNG_BYTES + b"\x00" * (600 * 1024),
            content_type="image/png",
        )
        self.assertEqual(status, 413)
        self.assertIn("超过", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
