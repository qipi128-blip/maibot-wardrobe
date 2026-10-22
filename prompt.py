"""提示词与聊天文案生成。"""

from __future__ import annotations

from datetime import date
from typing import Iterable

import re

from .calendar_rules import WEEKDAY_CN, season_label
from .wardrobe import Outfit, Selection

_SENTENCE_END_RE = re.compile(r"[。！？；!?;\n]")


def truncate_text(text: str, max_chars: int) -> str:
    """按句子边界截断文本，避免把一句话切成两半。"""

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

    return f"星期{WEEKDAY_CN[day.weekday()]}"


def build_identity_prompt(
    *,
    display_name: str,
    base_appearance: str,
    extra_traits: str,
    instruction: str,
    selection: Selection | None,
    outfit: Outfit | None,
    day: date,
    include_appearance: bool = True,
    include_outfit: bool = True,
    include_event: bool = True,
    event_names: Iterable[str] = (),
    has_selfie_tool: bool = False,
    max_chars: int = 800,
) -> str:
    """组装注入模型的形象提示词。"""

    header_suffix = ""
    if selection is not None and selection.season:
        header_suffix = f" · {season_label(selection.season)}"
    lines: list[str] = [f"【你此刻的形象｜{day.isoformat()} {weekday_label(day)}{header_suffix}】"]

    name = str(display_name or "").strip()
    if name:
        lines.append(f"你的名字：{name}。")

    appearance = " ".join(
        part.strip() for part in (str(base_appearance or "").strip(), str(extra_traits or "").strip()) if part.strip()
    )
    if include_appearance and appearance:
        lines.append(f"你的样子：{appearance}")

    if include_outfit and outfit is not None:
        detail = outfit.prompt_description()
        if detail:
            lines.append(f"你今天的穿着：「{outfit.display_name}」——{detail}")
        else:
            lines.append(f"你今天的穿着：「{outfit.display_name}」。")
    elif include_outfit and selection is not None:
        lines.append("你今天还没有可以穿的衣服数据。")

    names = [str(item).strip() for item in event_names if str(item).strip()]
    if include_event and names:
        event_text = "、".join(names)
        if selection is not None and selection.note:
            event_text += f"。{selection.note}"
        lines.append(f"今天的特别事件：{event_text}。")

    if str(instruction or "").strip():
        lines.append(str(instruction).strip())

    if has_selfie_tool:
        lines.append(
            "如果聊天里有人想看你的照片，或者话题正好聊到穿搭，你可以调用 "
            "wardrobe_send_selfie 工具把今天的照片发过去；不想发就不要调用。"
        )

    return truncate_text("\n".join(line for line in lines if line.strip()), max_chars)


def build_selfie_caption(
    template: str,
    *,
    name: str,
    outfit: Outfit | None,
    selection: Selection | None,
    event_names: Iterable[str] = (),
) -> str:
    """填充自拍文案模板。"""

    names = [str(item).strip() for item in event_names if str(item).strip()]
    event_text = "、".join(names) if names else ""
    values = {
        "name": str(name or "").strip() or "我",
        "outfit": outfit.display_name if outfit is not None else "今天的衣服",
        "season": season_label(selection.season) if selection is not None else "",
        "event": event_text,
    }
    try:
        rendered = str(template or "").format_map(values)
    except (KeyError, IndexError, ValueError):
        rendered = f"{values['name']}今天穿的是「{values['outfit']}」～"
    return rendered.strip()


def build_event_line(day: date, events: Iterable[object]) -> str:
    """把若干个事件格式化成一行。"""

    names = [str(getattr(event, "name", "") or getattr(event, "id", "")) for event in events]
    names = [name for name in names if name]
    if not names:
        return ""
    return f"{day.isoformat()}（{weekday_label(day)}）：" + "、".join(names)


def outcome_source_label(source: str) -> str:
    """把选衣来源翻译成中文。"""

    return {
        "event": "特殊日子指定",
        "season": "当季轮换",
        "all": "全衣橱轮换",
    }.get(str(source or ""), str(source or "未知"))
