"""图片格式识别、保存与读取。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import base64
import hashlib


class ImageError(ValueError):
    """图片不合法或保存失败。"""


_EXTENSION_BY_FORMAT = {
    "png": ".png",
    "jpeg": ".jpg",
    "gif": ".gif",
    "webp": ".webp",
}

_MIME_BY_FORMAT = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def detect_image_format(data: bytes) -> str | None:
    """根据文件头识别图片格式；不支持时返回 ``None``。"""

    if not data:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def mime_type_for_format(image_format: str) -> str:
    """返回图片格式对应的 MIME 类型。"""

    return _MIME_BY_FORMAT.get(str(image_format or "").lower(), "image/png")


def save_image(
    data_dir: Path,
    outfit_id: str,
    data: bytes,
    *,
    max_bytes: int,
    max_total_bytes: int = 0,
) -> tuple[str, str]:
    """把图片保存到 ``outfits/<outfit_id>/``，返回 ``(相对路径, 格式)``。

    - 相同内容会去重，直接复用已有文件；
    - ``max_bytes`` 限制单张大小；
    - ``max_total_bytes`` 大于 0 时限制整个图片目录大小。
    """

    if not data:
        raise ImageError("图片内容为空")
    if max_bytes > 0 and len(data) > max_bytes:
        raise ImageError(f"图片太大（{len(data) / 1024 / 1024:.1f} MB），上限是 {max_bytes / 1024 / 1024:.1f} MB")

    image_format = detect_image_format(data)
    if image_format is None:
        raise ImageError("只支持 PNG、JPEG、GIF、WEBP 格式的图片")

    outfits_root = (Path(data_dir) / "outfits").resolve()
    target_dir = (outfits_root / outfit_id).resolve()
    try:
        target_dir.relative_to(outfits_root)
    except ValueError as exc:
        raise ImageError("非法的衣服 ID") from exc
    target_dir.mkdir(parents=True, exist_ok=True)

    if max_total_bytes > 0 and _directory_size_bytes(outfits_root) + len(data) > max_total_bytes:
        raise ImageError("衣橱图片总大小已达到配置上限，请先删除一些旧图片")

    digest = hashlib.sha256(data).hexdigest()
    file_name = f"{digest[:20]}{_EXTENSION_BY_FORMAT[image_format]}"
    target_path = target_dir / file_name
    relative_path = f"outfits/{outfit_id}/{file_name}"
    if not target_path.exists():
        target_path.write_bytes(data)
    return relative_path, image_format


def load_image_base64(data_dir: Path, relative_path: str) -> tuple[str, str] | None:
    """读取图片并返回 ``(格式, base64)``；文件不存在或格式不支持时返回 ``None``。"""

    path = resolve_image_path(data_dir, relative_path)
    if path is None or not path.is_file():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    image_format = detect_image_format(data)
    if image_format is None:
        return None
    return image_format, base64.b64encode(data).decode("ascii")


def resolve_image_path(data_dir: Path, relative_path: str) -> Path | None:
    """把相对路径安全地解析到数据目录内。"""

    normalized = str(relative_path or "").replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or "../" in normalized:
        return None
    root = Path(data_dir).resolve()
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def delete_image(data_dir: Path, relative_path: str) -> bool:
    """删除数据目录内的一张图片。"""

    path = resolve_image_path(data_dir, relative_path)
    if path is None:
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def prune_outfit_images(
    data_dir: Path,
    images: Iterable[str],
    *,
    keep: int,
) -> tuple[list[str], list[str]]:
    """保留最近上传的 ``keep`` 张图片，返回 ``(保留列表, 被删除的文件)``。

    ``images`` 的顺序按上传先后排列，越靠后越新。
    """

    ordered = [str(item) for item in images]
    if keep <= 0 or len(ordered) <= keep:
        return ordered, []
    removed = ordered[: len(ordered) - keep]
    for relative_path in removed:
        delete_image(data_dir, relative_path)
    return ordered[len(ordered) - keep :], removed


def _directory_size_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total
