"""提示词生成。"""

from __future__ import annotations

from datetime import date
from typing import Iterable

import re

from .wardrobe import Outfit, Selection

_SENTENCE_END_RE = re.compile(r"[。！？；!?;\n]")
_WEEKDAY_CN = ("一", "二", "三", "四", "五", "六", "日")


def truncate_text(text: str, max_chars: int) -> str:
    """按句子边界截断文本。"""

    normalized = str(text or "").strip()
    limit = max(1, int(max_chars))
    if len(normalized) <= limit:
        return normalized
    head = normalized[:limit]
    matches = list(_SENTENCE_END_RE.finditer(head))
    if matches and matches[-1].end() > limit // 3:
        return head[: matches[-1].end()].rstrip()
    return head[: max(0, limit - 1)].rstrip() + "…"


def weekday_label(day: date) -> str:
    """返回「星期四」这样的中文星期。"""

    return f"星期{_WEEKDAY_CN[day.weekday()]}"


def build_identity_prompt(
    *,
    display_name: str,
    base_appearance: str,
    extra_traits: str,
    instruction: str,
    selection: Selection | None,
    outfit: Outfit | None,
    day: date,
    event_names: Iterable[str] = (),
    max_chars: int = 600,
) -> str:
    """组装注入模型的形象提示词。"""

    lines: list[str] = [f"【你此刻的形象｜{day.isoformat()} {weekday_label(day)}】"]

    name = str(display_name or "").strip()
    if name:
        lines.append(f"你的名字：{name}。")

    appearance = " ".join(
        part.strip() for part in (str(base_appearance or "").strip(), str(extra_traits or "").strip()) if part.strip()
    )
    if appearance:
        lines.append(f"你的样子：{appearance}")

    if outfit is not None:
        detail = outfit.prompt_description()
        if detail:
            lines.append(f"你今天的穿着：「{outfit.display_name}」——{detail}")
        else:
            lines.append(f"你今天的穿着：「{outfit.display_name}」。")
    elif selection is not None:
        lines.append("你今天还没有可以穿的衣服数据。")

    names = [str(item).strip() for item in event_names if str(item).strip()]
    if names:
        event_text = "、".join(names)
        if selection is not None and selection.note:
            event_text += f"。{selection.note}"
        lines.append(f"今天的特别事件：{event_text}。")

    if str(instruction or "").strip():
        lines.append(str(instruction).strip())

    return truncate_text("\n".join(line for line in lines if line.strip()), max_chars)
