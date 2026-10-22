"""测试用的假 Context 与辅助对象。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_BLANK_LOGGER = logging.getLogger("maibot_wardrobe.tests")


class FakeSend:
    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []
        self.images: list[tuple[str, str]] = []

    async def text(self, text: str, stream_id: str, **kwargs: Any) -> bool:
        self.texts.append((stream_id, text))
        return True

    async def image(self, image_data: str, stream_id: str, **kwargs: Any) -> bool:
        self.images.append((stream_id, image_data))
        return True


class FakeMessageApi:
    def __init__(self) -> None:
        self.messages: dict[str, dict[str, Any]] = {}

    async def get_by_id(self, message_id: str, **kwargs: Any) -> dict[str, Any] | None:
        return self.messages.get(message_id)


class FakeConfigApi:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = values or {}

    async def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


class FakeLLM:
    def __init__(self, response: str = "白色连衣裙，黑色长发。", success: bool = True) -> None:
        self.response = response
        self.success = success
        self.calls: list[dict[str, Any]] = []

    async def generate(self, prompt: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, **kwargs})
        return {
            "success": self.success,
            "response": self.response,
            "error": "" if self.success else "boom",
        }


class FakePaths:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.runtime_dir = data_dir / "runtime"


class FakeContext:
    def __init__(self, data_dir: Path, *, nickname: str = "大肥鱼") -> None:
        self.plugin_id = "github.qipi128-blip.wardrobe"
        self.logger = _BLANK_LOGGER
        self.paths = FakePaths(data_dir)
        self.send = FakeSend()
        self.message = FakeMessageApi()
        self.llm = FakeLLM()
        self.config = FakeConfigApi({"bot.nickname": nickname})


# 1x1 透明 PNG
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
