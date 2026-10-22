from __future__ import annotations

import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_package

load_package()

from maibot_wardrobe.images import (  # noqa: E402
    detect_image_format,
    load_image_for_vision,
    save_image,
)

try:
    from PIL import Image

    HAS_PIL = True
except Exception:  # noqa: BLE001
    HAS_PIL = False


def build_large_png() -> bytes:
    image = Image.new("RGB", (2000, 1200), (240, 220, 200))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class ImageTests(unittest.TestCase):
    def test_detect_formats(self) -> None:
        self.assertEqual(detect_image_format(b"\x89PNG\r\n\x1a\nabc"), "png")
        self.assertEqual(detect_image_format(b"\xff\xd8\xffabc"), "jpeg")
        self.assertEqual(detect_image_format(b"GIF89aabc"), "gif")
        self.assertEqual(detect_image_format(b"RIFF1234WEBPabc"), "webp")
        self.assertIsNone(detect_image_format(b"hello"))

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_vision_image_is_resized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data = build_large_png()
            relative, _fmt = save_image(root, "look", data, max_bytes=20 * 1024 * 1024)
            loaded = load_image_for_vision(root, relative, max_side=512, jpeg_quality=80)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            image_format, image_base64 = loaded
            self.assertEqual(image_format, "jpeg")
            with Image.open(io.BytesIO(base64.b64decode(image_base64))) as image:
                self.assertLessEqual(max(image.size), 512)
                self.assertEqual(image.format, "JPEG")

    def test_traversal_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertIsNone(load_image_for_vision(Path(temp_dir), "../secret.png"))


if __name__ == "__main__":
    unittest.main()
