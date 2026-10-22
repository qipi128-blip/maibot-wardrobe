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

from maibot_wardrobe.images import detect_image_format, load_image_for_vision, save_image  # noqa: E402

try:
    from PIL import Image

    HAS_PIL = True
except Exception:  # noqa: BLE001
    HAS_PIL = False


class MediaTests(unittest.TestCase):
    def test_detect_formats(self) -> None:
        self.assertEqual(detect_image_format(b"\x89PNG\r\n\x1a\nabc"), "png")
        self.assertEqual(detect_image_format(b"\xff\xd8\xffabc"), "jpeg")
        self.assertEqual(detect_image_format(b"GIF89aabc"), "gif")
        self.assertEqual(detect_image_format(b"RIFF1234WEBPabc"), "webp")
        self.assertIsNone(detect_image_format(b"hello"))

    @unittest.skipUnless(HAS_PIL, "需要 Pillow")
    def test_vision_resize(self) -> None:
        image = Image.new("RGB", (2000, 1200), (240, 220, 200))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            relative, _fmt = save_image(root, "look", buffer.getvalue(), max_bytes=20 * 1024 * 1024)
            loaded = load_image_for_vision(root, relative, max_side=512)
            assert loaded is not None
            image_format, image_base64 = loaded
            self.assertEqual(image_format, "jpeg")
            with Image.open(io.BytesIO(base64.b64decode(image_base64))) as result:
                self.assertLessEqual(max(result.size), 512)

    def test_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertIsNone(load_image_for_vision(Path(temp_dir), "../secret.png"))


if __name__ == "__main__":
    unittest.main()
