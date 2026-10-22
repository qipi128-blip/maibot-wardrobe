"""每日穿搭状态的持久化。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import json
import os

from .wardrobe import Selection

_STATE_VERSION = 1
_MAX_HISTORY = 180


@dataclass
class DailyState:
    """保存「今天穿什么」以及最近的历史记录。"""

    path: Path
    version: int = _STATE_VERSION
    today: Selection | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "DailyState":
        state = cls(path=Path(path))
        try:
            raw = json.loads(state.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return state
        except Exception:
            return state

        if not isinstance(raw, dict):
            return state
        state.version = int(raw.get("version", _STATE_VERSION) or _STATE_VERSION)
        today_raw = raw.get("today")
        if isinstance(today_raw, dict):
            state.today = Selection.from_dict(today_raw)
        history_raw = raw.get("history")
        if isinstance(history_raw, list):
            for item in history_raw:
                if not isinstance(item, dict):
                    continue
                day = str(item.get("date", "") or "").strip()
                outfit_id = str(item.get("outfit_id", "") or "").strip()
                if day and outfit_id:
                    state.history.append({"date": day, "outfit_id": outfit_id})
        state.history = state.history[-_MAX_HISTORY:]
        return state

    def ensure_today(self, day: date, picker: Callable[[], Selection | None]) -> Selection | None:
        """确保当天有一套穿搭；跨天时自动抽取并写入历史。"""

        day_text = day.isoformat()
        if self.today is not None and self.today.day == day_text:
            return self.today

        selection = picker()
        if selection is None:
            return None
        self.today = selection
        self._record(selection)
        self.save()
        return selection

    def reroll(self, day: date, picker: Callable[[], Selection | None]) -> Selection | None:
        """强制重新抽取当天的穿搭。"""

        selection = picker()
        if selection is None:
            return None
        selection.day = day.isoformat()
        self.today = selection
        self._record(selection)
        self.save()
        return selection

    def recent_outfit_ids(self, day: date, days: int) -> set[str]:
        """返回最近 ``days`` 天穿过的衣服 ID（不含当天）。"""

        if days <= 0:
            return set()
        cutoff = day - timedelta(days=max(1, int(days)))
        result: set[str] = set()
        for item in self.history:
            try:
                item_day = date.fromisoformat(item["date"])
            except (ValueError, KeyError):
                continue
            if cutoff <= item_day < day:
                result.add(item["outfit_id"])
        return result

    def previous_outfit_id(self, day: date) -> str:
        """返回今天之前最近一次穿的衣服 ID。"""

        candidates = [item for item in self.history if item["date"] < day.isoformat()]
        if not candidates:
            return ""
        candidates.sort(key=lambda item: item["date"])
        return candidates[-1]["outfit_id"]

    def _record(self, selection: Selection) -> None:
        self.history = [item for item in self.history if item.get("date") != selection.day]
        self.history.append({"date": selection.day, "outfit_id": selection.outfit_id})
        self.history.sort(key=lambda item: item.get("date", ""))
        self.history = self.history[-_MAX_HISTORY:]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "version": _STATE_VERSION,
            "today": self.today.to_dict() if self.today is not None else None,
            "history": self.history[-_MAX_HISTORY:],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        temp_path.write_text(text, encoding="utf-8")
        os.replace(temp_path, self.path)
