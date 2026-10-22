"""衣橱数据模型、TOML 读写与每日随机选衣。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import hashlib
import json
import os
import random
import time
import tomllib

from .calendar_rules import active_events

_DEFAULT_HEADER = """# 「她的衣橱」数据文件
#
# 这个文件由插件自动生成，也可以在网页里编辑；保存后下一次读取会自动生效。
#
# 一套衣服：
#   [[outfits]]
#   id = "spring_dress"          # 必填，只能使用字母、数字、下划线和短横线
#   name = "春日白裙"
#   description = "……"          # 给模型看的穿搭描述
#   images = []                  # 相对 data/plugins/<id>/ 的图片路径，网页上传自动维护
#   vision_description = ""      # 视觉模型自动生成的描述
#
# 一个特殊事件：
#   [[events]]
#   id = "birthday"
#   name = "生日"
#   date = "10-28"               # 10-28 / 2026-10-28 / 12-24..12-26 / 2026-12-24..2026-12-26
#   outfits = ["birthday_dress"] # 指定几套衣服，从中随机抽
#   note = "今天是生日，穿了白色连衣裙。"
#   enabled = true
"""


class WardrobeError(ValueError):
    """衣橱文件格式错误。"""


def safe_outfit_id(raw: str) -> str:
    """校验并返回安全的衣服 ID。"""

    value = str(raw or "").strip()
    if not value:
        raise ValueError("衣服 ID 不能为空")
    if not all(char.isascii() and (char.isalnum() or char in "_-") for char in value):
        raise ValueError("衣服 ID 只能使用英文字母、数字、下划线和短横线")
    if len(value) > 48:
        raise ValueError("衣服 ID 太长（最多 48 个字符）")
    return value


def _normalize_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_images(value: Any) -> list[str]:
    images: list[str] = []
    for item in _normalize_str_list(value):
        normalized = item.replace("\\", "/").strip()
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if not normalized or normalized.startswith("../") or "/../" in normalized:
            continue
        if normalized not in images:
            images.append(normalized)
    return images


@dataclass
class Outfit:
    """一套衣服。"""

    id: str
    name: str = ""
    description: str = ""
    images: list[str] = field(default_factory=list)
    vision_description: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Outfit":
        outfit_id = safe_outfit_id(str(raw.get("id", "")))
        return cls(
            id=outfit_id,
            name=str(raw.get("name", "") or "").strip() or outfit_id,
            description=str(raw.get("description", "") or "").strip(),
            images=_normalize_images(raw.get("images")),
            vision_description=str(raw.get("vision_description", "") or "").strip(),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "images": list(self.images),
            "vision_description": self.vision_description,
        }

    @property
    def display_name(self) -> str:
        return self.name or self.id

    def prompt_description(self) -> str:
        parts = [self.description, self.vision_description]
        return "；".join(part.strip().rstrip("。；") for part in parts if part and part.strip())


@dataclass
class WardrobeEvent:
    """一个特殊事件。"""

    id: str
    name: str = ""
    date: str = ""
    outfits: list[str] = field(default_factory=list)
    note: str = ""
    enabled: bool = True

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "WardrobeEvent":
        event_id = str(raw.get("id", "") or "").strip()
        if not event_id:
            raise WardrobeError("事件缺少 id")
        date_text = str(raw.get("date", "") or "").strip()
        if not date_text:
            # 兼容 1.x 的 dates/ranges 字段，优先取第一个日期或区间。
            dates = _normalize_str_list(raw.get("dates"))
            ranges = _normalize_str_list(raw.get("ranges"))
            date_text = dates[0] if dates else (ranges[0] if ranges else "")
        return cls(
            id=event_id,
            name=str(raw.get("name", "") or "").strip() or event_id,
            date=date_text,
            outfits=[str(item).strip() for item in _normalize_str_list(raw.get("outfits")) if str(item).strip()],
            note=str(raw.get("note", "") or "").strip(),
            enabled=bool(raw.get("enabled", True)),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "date": self.date,
            "outfits": list(self.outfits),
            "note": self.note,
            "enabled": self.enabled,
        }


@dataclass
class Wardrobe:
    """整个衣橱。"""

    outfits: list[Outfit] = field(default_factory=list)
    events: list[WardrobeEvent] = field(default_factory=list)

    def get_outfit(self, outfit_id: str) -> Outfit | None:
        normalized = str(outfit_id or "").strip()
        for outfit in self.outfits:
            if outfit.id == normalized:
                return outfit
        return None

    def get_event(self, event_id: str) -> WardrobeEvent | None:
        normalized = str(event_id or "").strip()
        for event in self.events:
            if event.id == normalized:
                return event
        return None

    @property
    def enabled_outfits(self) -> list[Outfit]:
        return list(self.outfits)

    def ensure_outfit(self, outfit_id: str, name: str = "", description: str = "") -> tuple[Outfit, bool]:
        """返回指定 ID 的衣服；不存在时创建。"""

        existing = self.get_outfit(outfit_id)
        if existing is not None:
            return existing, False
        outfit = Outfit(
            id=safe_outfit_id(outfit_id),
            name=name.strip() or outfit_id,
            description=description.strip(),
        )
        self.outfits.append(outfit)
        return outfit, True

    def remove_outfit(self, outfit_id: str) -> bool:
        before = len(self.outfits)
        self.outfits = [outfit for outfit in self.outfits if outfit.id != outfit_id]
        return len(self.outfits) != before

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Wardrobe":
        outfits: list[Outfit] = []
        events: list[WardrobeEvent] = []
        seen_ids: set[str] = set()
        for item in raw.get("outfits", []) or []:
            if not isinstance(item, Mapping):
                continue
            outfit = Outfit.from_mapping(item)
            if outfit.id in seen_ids:
                raise WardrobeError(f"衣服 ID 重复: {outfit.id}")
            seen_ids.add(outfit.id)
            outfits.append(outfit)

        seen_events: set[str] = set()
        for item in raw.get("events", []) or []:
            if not isinstance(item, Mapping):
                continue
            event = WardrobeEvent.from_mapping(item)
            if event.id in seen_events:
                raise WardrobeError(f"事件 ID 重复: {event.id}")
            seen_events.add(event.id)
            events.append(event)
        return cls(outfits=outfits, events=events)

    @classmethod
    def from_toml(cls, text: str) -> "Wardrobe":
        try:
            raw = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise WardrobeError(f"TOML 解析失败: {exc}") from exc
        if not isinstance(raw, dict):
            raise WardrobeError("衣橱文件格式不正确")
        return cls.from_mapping(raw)

    def to_toml(self) -> str:
        lines: list[str] = [_DEFAULT_HEADER.rstrip(), ""]
        for outfit in self.outfits:
            lines.append("[[outfits]]")
            for key, value in outfit.to_mapping().items():
                lines.append(f"{key} = {_toml_value(value)}")
            lines.append("")

        for event in self.events:
            lines.append("[[events]]")
            for key, value in event.to_mapping().items():
                lines.append(f"{key} = {_toml_value(value)}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


class WardrobeStore:
    """负责 ``wardrobe.toml`` 的加载、热更新与保存。"""

    def __init__(self, path: Path, *, template_path: Path | None = None, logger: Any = None) -> None:
        self.path = Path(path)
        self.template_path = Path(template_path) if template_path else None
        self.logger = logger
        self.wardrobe = Wardrobe()
        self.last_error = ""
        self._mtime_ns = -1
        self._size = -1
        self._loaded = False

    def ensure_loaded(self, *, force: bool = False) -> Wardrobe:
        try:
            stat = self.path.stat()
            current_mtime = stat.st_mtime_ns
            current_size = stat.st_size
        except FileNotFoundError:
            current_mtime = -1
            current_size = -1

        if not force and self._loaded and current_mtime == self._mtime_ns and current_size == self._size:
            return self.wardrobe

        if not self.path.exists():
            self._initialize_file()
            try:
                stat = self.path.stat()
                current_mtime = stat.st_mtime_ns
                current_size = stat.st_size
            except FileNotFoundError:
                current_mtime = -1
                current_size = -1

        try:
            text = self.path.read_text(encoding="utf-8")
            self.wardrobe = Wardrobe.from_toml(text)
            self.last_error = ""
            self._mtime_ns = current_mtime
            self._size = current_size
            self._loaded = True
            if self.logger is not None:
                self.logger.info(
                    "衣橱已加载：%d 套衣服，%d 个事件", len(self.wardrobe.outfits), len(self.wardrobe.events)
                )
        except Exception as exc:  # noqa: BLE001 - 用户文件错误不应中断聊天
            self.last_error = str(exc)
            if self.logger is not None:
                self.logger.warning("衣橱文件读取失败，将继续使用上一次成功加载的数据: %s", exc)
            if not self._loaded:
                self.wardrobe = Wardrobe()
        return self.wardrobe

    def save(self, wardrobe: Wardrobe | None = None) -> None:
        target = wardrobe if wardrobe is not None else self.wardrobe
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = target.to_toml()
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        temp_path.write_text(text, encoding="utf-8")
        os.replace(temp_path, self.path)
        self.wardrobe = target
        self._loaded = True
        try:
            stat = self.path.stat()
            self._mtime_ns = stat.st_mtime_ns
            self._size = stat.st_size
        except FileNotFoundError:
            self._mtime_ns = -1
            self._size = -1

    def _initialize_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.template_path is not None and self.template_path.is_file():
            text = self.template_path.read_text(encoding="utf-8")
        else:
            text = Wardrobe().to_toml()
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        temp_path.write_text(text, encoding="utf-8")
        os.replace(temp_path, self.path)


@dataclass
class Selection:
    """某一天的选衣结果。"""

    day: str
    outfit_id: str
    source: str
    event_ids: list[str] = field(default_factory=list)
    note: str = ""
    picked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.day,
            "outfit_id": self.outfit_id,
            "source": self.source,
            "event_ids": list(self.event_ids),
            "note": self.note,
            "picked_at": self.picked_at or time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Selection | None":
        outfit_id = str(raw.get("outfit_id", "") or "").strip()
        day = str(raw.get("date", "") or "").strip()
        if not outfit_id or not day:
            return None
        return cls(
            day=day,
            outfit_id=outfit_id,
            source=str(raw.get("source", "") or ""),
            event_ids=_normalize_str_list(raw.get("event_ids")),
            note=str(raw.get("note", "") or ""),
            picked_at=str(raw.get("picked_at", "") or ""),
        )


def _random_pick(pool: Sequence[Outfit], *, seed_text: str) -> Outfit:
    ids = sorted(outfit.id for outfit in pool)
    digest = hashlib.sha256(f"{seed_text}|{','.join(ids)}".encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    return rng.choice(list(pool))


def select_outfit(
    wardrobe: Wardrobe,
    day: date,
    *,
    avoid_ids: Iterable[str] = (),
    avoid_repeat_days: int = 3,
    seed: str = "",
) -> Selection | None:
    """为指定日期挑选一套衣服。

    有事件时优先事件指定的衣服；没有事件时从全部衣服里随机。
    同一天、同一候选池的结果是稳定的。
    """

    enabled = wardrobe.enabled_outfits
    if not enabled:
        return None

    matched_events = active_events(wardrobe.events, day)
    event_ids = [event.id for event in matched_events]
    notes = "；".join(event.note for event in matched_events if event.note)

    pool: list[Outfit] = []
    source = "daily"
    for event in matched_events:
        for outfit_id in event.outfits:
            outfit = wardrobe.get_outfit(outfit_id)
            if outfit is not None and outfit not in pool:
                pool.append(outfit)
    if pool:
        source = "event"
    else:
        pool = list(enabled)

    avoid = {str(item) for item in avoid_ids if str(item)}
    if avoid_repeat_days > 0 and avoid:
        filtered = [outfit for outfit in pool if outfit.id not in avoid]
        if filtered:
            pool = filtered

    chosen = _random_pick(pool, seed_text=f"{seed}|{day.isoformat()}|{source}")
    return Selection(
        day=day.isoformat(),
        outfit_id=chosen.id,
        source=source,
        event_ids=event_ids,
        note=notes,
        picked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
