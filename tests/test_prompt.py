from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_package

load_package()

from maibot_wardrobe.prompt import build_identity_prompt, truncate_text  # noqa: E402
from maibot_wardrobe.wardrobe import Outfit, Selection  # noqa: E402


class PromptTests(unittest.TestCase):
    def test_contains_identity_outfit_and_event(self) -> None:
        selection = Selection(
            day="2026-10-28",
            outfit_id="dress",
            season="autumn",
            source="event",
            event_ids=["birthday"],
            note="你特意换上了白色连衣裙。",
        )
        outfit = Outfit(id="dress", name="生日礼服", description="白色连衣裙，珍珠耳钉")
        text = build_identity_prompt(
            display_name="大肥鱼",
            base_appearance="十九岁的女生，及肩黑发。",
            extra_traits="左手腕有一根红绳。",
            instruction="这是你本人真实的样子。",
            selection=selection,
            outfit=outfit,
            day=date(2026, 10, 28),
            event_names=["生日"],
        )
        self.assertIn("【你此刻的形象", text)
        self.assertIn("大肥鱼", text)
        self.assertIn("十九岁的女生", text)
        self.assertIn("生日礼服", text)
        self.assertIn("白色连衣裙", text)
        self.assertIn("生日", text)
        self.assertIn("你特意换上了白色连衣裙", text)
        self.assertIn("这是你本人真实的样子", text)

    def test_truncate_on_sentence_boundary(self) -> None:
        text = "第一句。" + "第二句很长。" * 20
        truncated = truncate_text(text, 20)
        self.assertTrue(truncated.endswith("。"))
        self.assertLessEqual(len(truncated), 20)

    def test_max_chars_respected(self) -> None:
        text = build_identity_prompt(
            display_name="名字",
            base_appearance="设定" * 500,
            extra_traits="",
            instruction="",
            selection=None,
            outfit=None,
            day=date(2026, 10, 22),
            max_chars=200,
        )
        self.assertLessEqual(len(text), 200)


if __name__ == "__main__":
    unittest.main()
