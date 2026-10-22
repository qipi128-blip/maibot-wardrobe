from __future__ import annotations

import asyncio
import base64
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


class WebUiTests(unittest.IsolatedAsyncioTestCase):
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
            separator = "&" if "?" in path else "?"
            url += f"{separator}token={urllib.parse.quote(actual_token)}"
        headers = {"Content-Type": content_type} if data is not None else {}
        request = urllib.request.Request(url, data=data, headers=headers, method=method)

        def run() -> tuple[int, bytes, dict[str, str]]:
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    return response.status, response.read(), dict(response.headers)
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read(), dict(exc.headers)

        return await asyncio.to_thread(run)

    async def test_home_card_registered(self) -> None:
        components = {component["name"]: component for component in self.plugin.get_components()}
        self.assertIn("wardrobe_web_upload", components)
        card = components["wardrobe_web_upload"]
        self.assertEqual(card["type"], "HOME_CARD")
        self.assertIn(f"127.0.0.1:{self.plugin._web_port}", card["metadata"]["link_url"])
        self.assertIn(self.plugin._web_token, card["metadata"]["link_url"])

    async def test_state_and_page(self) -> None:
        status, body, _headers = await self.request("/api/state")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["name"], "大肥鱼")
        self.assertEqual(payload["date"], "2026-10-22")
        self.assertEqual(len(payload["outfits"]), 8)

        status, body, _headers = await self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        self.assertIn("她的衣橱", page)
        self.assertIn("上传图片", page)

    async def test_token_required(self) -> None:
        status, _body, _headers = await self.request("/api/state", token="")
        self.assertEqual(status, 403)

    async def test_upload_and_serve_image(self) -> None:
        status, body, _headers = await self.request(
            "/api/upload?outfit_id=web_test&name="
            + urllib.parse.quote("网页测试")
            + "&caption="
            + urllib.parse.quote("一条测试裙子"),
            method="POST",
            data=PNG_BYTES,
            content_type="image/png",
        )
        self.assertEqual(status, 200, body[:300])
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["outfit_id"], "web_test")
        self.assertEqual(payload["saved"], 1)

        wardrobe = self.plugin._store.wardrobe  # type: ignore[union-attr]
        outfit = wardrobe.get_outfit("web_test")
        self.assertIsNotNone(outfit)
        self.assertEqual(len(outfit.images), 1)  # type: ignore[union-attr]
        self.assertTrue((self.data_dir / outfit.images[0]).is_file())  # type: ignore[union-attr]

        status, body, headers = await self.request("/image?outfit_id=web_test&index=0")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))

        status, body, _headers = await self.request("/api/state")
        state = json.loads(body)
        ids = {item["id"] for item in state["outfits"]}
        self.assertIn("web_test", ids)

    async def test_reject_non_image(self) -> None:
        status, body, _headers = await self.request(
            "/api/upload?outfit_id=bad_test",
            method="POST",
            data=b"not an image",
            content_type="text/plain",
        )
        self.assertEqual(status, 400)
        self.assertIn("只支持", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
