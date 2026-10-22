"""「她的衣橱」—— 让麦麦知道自己长什么样，并且每天换一套衣服。

插件把「外貌设定 + 今日穿搭 + 特殊日子」注入 planner / replyer 的系统提示词，
因此即使回复模型不是视觉模型，也能通过视觉模型生成的文字描述知道自己此刻的样子。
同时提供上传穿搭图、每日轮换、季节/节日事件、自拍命令与模型工具。
"""

from __future__ import annotations

from datetime import date, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import base64
import random
import re
import secrets
import socket
import time
import uuid

from maibot_sdk import Command, HookHandler, MaiBotPlugin, Tool
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

from .calendar_rules import (
    active_events,
    has_lunar_support,
    parse_iso_date,
    season_label,
    season_of,
    upcoming_events,
)
from .config import WardrobeConfig
from .images import ImageError, load_image_base64, load_image_for_vision, prune_outfit_images, save_image
from .prompt import (
    build_identity_prompt,
    build_selfie_caption,
    outcome_source_label,
    weekday_label,
)
from .state import DailyState
from .wardrobe import Outfit, Selection, Wardrobe, WardrobeStore, safe_outfit_id, select_outfit
from .webui import WardrobeWebServer

_PROMPT_MARKER = "【你此刻的形象"
_MAX_MESSAGE_CHARS = 1800
_MAX_UPLOAD_IMAGES = 4


def _safe_command(func: Callable[..., Awaitable[tuple[bool, str, int]]]) -> Callable[..., Awaitable[tuple[bool, str, int]]]:
    """命令异常兜底：任何异常都只回复一句提示，不打断主程序。"""

    @wraps(func)
    async def wrapper(self: "WardrobePlugin", **kwargs: Any) -> tuple[bool, str, int]:
        try:
            return await func(self, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.exception("衣橱命令执行失败: %s", exc)
            stream_id = str(kwargs.get("stream_id", "") or "")
            if stream_id:
                await self._say(stream_id, f"衣橱操作失败了：{exc}")
            return False, "", 2

    return wrapper


class WardrobePlugin(MaiBotPlugin):
    """她的衣橱插件。"""

    config_model = WardrobeConfig

    def __init__(self) -> None:
        super().__init__()
        self._data_dir: Path | None = None
        self._store: WardrobeStore | None = None
        self._state: DailyState | None = None
        self._display_name: str = ""
        self._last_session_id: str = ""
        self._last_session_at: float = 0.0
        self._last_selfie_at: dict[str, float] = {}
        self._lunar_warned = False
        self._started = False
        self._web_socket: socket.socket | None = None
        self._web_server: WardrobeWebServer | None = None
        self._web_port: int = 0
        self._web_token: str = secrets.token_urlsafe(24)

    # ------------------------------------------------------------------ 生命周期

    async def on_load(self) -> None:
        """加载衣橱与状态文件。"""

        try:
            self._data_dir = self._resolve_data_dir()
            self._data_dir.mkdir(parents=True, exist_ok=True)
            package_dir = Path(__file__).resolve().parent
            self._store = WardrobeStore(
                self._data_file(self.config.storage.wardrobe_file, "wardrobe.toml"),
                template_path=package_dir / "assets" / "default_wardrobe.toml",
                logger=self.ctx.logger,
            )
            self._store.ensure_loaded(force=True)
            self._state = DailyState.load(self._data_file(self.config.storage.state_file, "state.json"))
            await self._resolve_display_name()
            self._started = True

            if not self.config.plugin.enabled:
                self.ctx.logger.info("「她的衣橱」已加载，但插件未启用")
                return

            await self._start_web_server()
            today = self._today()
            selection = self._ensure_today(today)
            wardrobe = self._store.wardrobe
            self.ctx.logger.info(
                "「她的衣橱」已加载：%d 套衣服，%d 个事件；今日=%s",
                len(wardrobe.outfits),
                len(wardrobe.events),
                selection.outfit_id if selection else "（衣橱为空）",
            )
            if not has_lunar_support() and any(event.lunar_dates for event in wardrobe.events):
                self.ctx.logger.warning(
                    "衣橱里有农历事件，但当前环境未安装可选依赖 lunardate，农历规则会被跳过"
                )
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.exception("「她的衣橱」初始化失败: %s", exc)

    async def on_unload(self) -> None:
        """保存状态并关闭网页上传服务。"""

        try:
            await self._stop_web_server()
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("衣橱网页上传服务关闭失败: %s", exc)
        try:
            if self._state is not None:
                self._state.save()
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("衣橱状态保存失败: %s", exc)

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """配置热更新。"""

        del config_data, version
        if str(scope or "").lower() != "self":
            return
        try:
            package_dir = Path(__file__).resolve().parent
            self._data_dir = self._resolve_data_dir()
            wardrobe_path = self._data_file(self.config.storage.wardrobe_file, "wardrobe.toml")
            state_path = self._data_file(self.config.storage.state_file, "state.json")
            if self._store is None or self._store.path != wardrobe_path:
                self._store = WardrobeStore(
                    wardrobe_path,
                    template_path=package_dir / "assets" / "default_wardrobe.toml",
                    logger=self.ctx.logger,
                )
            if self._state is None or self._state.path != state_path:
                self._state = DailyState.load(state_path)
            if self._store is not None:
                self._store.ensure_loaded(force=True)
            self._display_name = ""
            await self._resolve_display_name()
            if not self.config.web_ui.enabled:
                await self._stop_web_server()
            elif self._web_server is None:
                await self._start_web_server()
            self.ctx.logger.info("「她的衣橱」配置已热更新")
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("「她的衣橱」配置热更新失败: %s", exc)

    # ------------------------------------------------------------------ 组件

    def get_components(self) -> list[dict[str, Any]]:
        """在标准组件之外注册 MaiBot 首页的「她的衣橱」入口卡片。"""

        components = super().get_components()
        try:
            if not self.config.plugin.enabled or not self.config.web_ui.enabled:
                return components
            if not self.config.web_ui.show_home_card:
                return components
            sock = self._ensure_web_socket()
            if sock is None:
                return components
            components.append(
                {
                    "name": "wardrobe_web_upload",
                    "type": "HOME_CARD",
                    "metadata": {
                        "title": self.config.web_ui.page_title or "她的衣橱",
                        "description": "在浏览器里上传穿搭图、查看麦麦今天的穿搭，不用在 QQ 里发指令。",
                        "content": "拖拽图片就能给她换装；也可以给现有衣服补图，或让视觉模型重新描述。",
                        "link_url": self._web_url(),
                        "link_label": "打开衣橱上传页",
                        "icon": "shirt",
                        "width": "medium",
                        "order": int(self.config.web_ui.home_card_order),
                        "show_title": True,
                    },
                }
            )
        except RuntimeError:
            # 配置/上下文尚未注入时（例如纯声明检查）只返回静态组件。
            return components
        except Exception as exc:  # noqa: BLE001
            self._get_logger().warning("衣橱首页卡片注册失败: %s", exc)
        return components

    # ------------------------------------------------------------------ 提示词注入

    @HookHandler(
        "maisaka.planner.before_request",
        name="wardrobe_planner_inject",
        description="把麦麦今日形象与穿搭注入 planner 上下文",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        timeout_ms=6000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_planner_before_request(self, **kwargs: Any) -> dict[str, Any]:
        """planner 请求前注入。"""

        return self._inject_identity(kwargs, target="planner")

    @HookHandler(
        "maisaka.replyer.before_model_request",
        name="wardrobe_replyer_inject",
        description="把麦麦今日形象与穿搭注入 replyer 请求",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        timeout_ms=6000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_replyer_before_model_request(self, **kwargs: Any) -> dict[str, Any]:
        """replyer 模型请求前注入。"""

        return self._inject_identity(kwargs, target="replyer")

    def _inject_identity(self, kwargs: dict[str, Any], *, target: str) -> dict[str, Any]:
        """把形象文本追加到第一条 SystemMessageItem；失败时原样返回。"""

        unchanged = {"action": "continue"}
        try:
            if not self.config.plugin.enabled or not self.config.injection.enabled:
                return unchanged
            if not bool(getattr(self.config.injection, target)):
                return unchanged

            session_id = str(kwargs.get("session_id", "") or "").strip()
            if session_id:
                self._remember_session(session_id)

            items = kwargs.get("items")
            if not isinstance(items, list) or not items:
                return unchanged

            block = self._build_prompt_block()
            if not block or self._has_prompt_marker(items):
                return unchanged

            self._append_system_text(items, block)
            return {"action": "continue", "modified_kwargs": kwargs}
        except Exception as exc:  # noqa: BLE001 - 注入失败不能影响聊天
            self.ctx.logger.warning("衣橱提示词注入失败: %s", exc)
            return unchanged

    @staticmethod
    def _has_prompt_marker(items: Sequence[Any]) -> bool:
        for item in items:
            if not isinstance(item, dict) or item.get("item_type") != "SystemMessageItem":
                continue
            for part in item.get("parts") or []:
                if isinstance(part, dict) and part.get("type") == "text":
                    if _PROMPT_MARKER in str(part.get("text", "") or ""):
                        return True
        return False

    @staticmethod
    def _append_system_text(items: list[Any], text: str) -> None:
        for item in items:
            if not isinstance(item, dict) or item.get("item_type") != "SystemMessageItem":
                continue
            parts = item.get("parts")
            if not isinstance(parts, list):
                parts = []
                item["parts"] = parts
            parts.append({"type": "text", "text": "\n\n" + text})
            return

        items.insert(
            0,
            {
                "item_type": "SystemMessageItem",
                "meta": {
                    "item_id": uuid.uuid4().hex,
                    "logical_turn_id": None,
                    "timestamp": datetime.now().isoformat(),
                },
                "parts": [{"type": "text", "text": text}],
            },
        )

    def _build_prompt_block(self) -> str:
        """生成当前日期对应的形象提示词。"""

        if not self.config.injection.enabled:
            return ""
        today = self._today()
        selection = self._ensure_today(today)
        outfit = self._outfit_of(selection)
        events = self._active_events(today, selection)
        event_names = [event.name for event in events]
        return build_identity_prompt(
            display_name=self._display_name or self.config.identity.display_name,
            base_appearance=self.config.identity.base_appearance,
            extra_traits=self.config.identity.extra_traits,
            instruction=self.config.identity.instruction,
            selection=selection,
            outfit=outfit,
            day=today,
            include_appearance=self.config.injection.include_appearance,
            include_outfit=self.config.injection.include_outfit,
            include_event=self.config.injection.include_event and self.config.event.show_in_prompt,
            event_names=event_names,
            has_selfie_tool=bool(self.config.selfie.enabled and self.config.selfie.allow_tool),
            max_chars=self.config.injection.max_chars,
        )

    # ------------------------------------------------------------------ 工具：自拍

    @Tool(
        "wardrobe_send_selfie",
        description="把麦麦今天的穿搭照片（自拍）发到当前聊天。有人想看麦麦长什么样、问她今天穿什么，或话题聊到穿搭时可以调用。",
        parameters={
            "outfit_id": {
                "type": "string",
                "description": "可选：指定要发的衣服 ID；留空表示今天正穿着的这套",
                "required": False,
            }
        },
    )
    async def tool_send_selfie(self, outfit_id: str = "", **kwargs: Any) -> dict[str, Any]:
        """模型工具：发送自拍。"""

        del kwargs
        try:
            if not self.config.plugin.enabled or not self.config.selfie.enabled:
                return {"content": "自拍功能当前没有开启。"}
            if not self.config.selfie.allow_tool:
                return {"content": "现在不方便发照片。"}
            stream_id = self._active_stream_id()
            if not stream_id:
                return {"content": "当前没有可用的聊天会话，先让用户用 /看看你 吧。"}
            ok, message = await self._send_selfie(stream_id, outfit_id=outfit_id, via_tool=True)
            return {"content": message if ok else f"照片没有发出去：{message}"}
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("衣橱自拍工具失败: %s", exc)
            return {"content": f"照片发送失败：{exc}"}

    # ------------------------------------------------------------------ 命令

    @Command(
        name="wardrobe_status",
        pattern=r"^\s*/衣橱(?:状态|status)?\s*$",
        description="查看麦麦今天的穿搭、季节和特殊事件",
    )
    @_safe_command
    async def cmd_status(self, **kwargs: Any) -> tuple[bool, str, int]:
        """今日穿搭。"""

        if not await self._command_ready(kwargs):
            return True, "", 2
        stream_id = self._stream_id(kwargs)
        today = self._today()
        selection = self._ensure_today(today)
        wardrobe = self._wardrobe()
        if selection is None or not wardrobe.outfits:
            await self._say(
                stream_id,
                "衣橱里还空空的。用 `/衣橱上传 <id> <名字>` 加一套衣服吧，例如：\n"
                "`/衣橱上传 spring_dress 春日白裙`（文字可以带图片，也可以回复一张图）",
            )
            return True, "", 2

        outfit = self._outfit_of(selection)
        events = self._active_events(today, selection)
        lines = [
            f"🐟 今日穿搭 · {today.isoformat()}（{weekday_label(today)}）",
            f"季节：{season_label(selection.season)}",
        ]
        if events:
            event_names = "、".join(event.name for event in events)
            lines.append(f"特殊日子：{event_names}")
            if selection.note:
                lines.append(f"说明：{selection.note}")
        else:
            lines.append("特殊日子：今天没有特别安排")
        lines.append(f"穿搭：{outfit.display_name if outfit else selection.outfit_id}")
        if outfit is not None:
            description = outfit.prompt_description()
            if description:
                lines.append(f"细节：{description}")
            lines.append(f"图片：{len(outfit.images)} 张")
        lines.append(f"来源：{outcome_source_label(selection.source)}")
        lines.append(f"衣橱：{len(wardrobe.outfits)} 套（启用 {len(wardrobe.enabled_outfits)} 套）")
        if self._store is not None and self._store.last_error:
            lines.append(f"⚠️ 衣橱文件读取失败：{self._store.last_error}")
        if not has_lunar_support() and any(event.lunar_dates for event in wardrobe.events):
            lines.append("⚠️ 未安装 lunardate，农历事件暂不可用")
        await self._say(stream_id, "\n".join(lines))
        return True, "", 2

    @Command(
        name="wardrobe_list",
        pattern=r"^\s*/衣橱(?:列表|list)\s*$",
        description="列出衣橱里的所有衣服",
    )
    @_safe_command
    async def cmd_list(self, **kwargs: Any) -> tuple[bool, str, int]:
        """衣橱列表。"""

        if not await self._command_ready(kwargs):
            return True, "", 2
        wardrobe = self._wardrobe()
        if not wardrobe.outfits:
            await self._say(self._stream_id(kwargs), "衣橱里还没有衣服。用 `/衣橱上传 <id> <名字>` 添加第一套吧。")
            return True, "", 2

        limit = max(5, int(self.config.commands.max_list_items))
        lines = [f"👗 衣橱（共 {len(wardrobe.outfits)} 套，启用 {len(wardrobe.enabled_outfits)} 套）"]
        for index, outfit in enumerate(wardrobe.outfits[:limit], start=1):
            marker = "✓" if outfit.enabled else "✗"
            if outfit.only_for_events:
                marker += "·仅事件"
            lines.append(
                f"{index}. {marker} {outfit.id}｜{outfit.display_name}｜{outfit.season_text()}｜"
                f"{len(outfit.images)} 张｜权重 {outfit.weight:g}"
            )
        if len(wardrobe.outfits) > limit:
            lines.append(f"……还有 {len(wardrobe.outfits) - limit} 套未显示")
        await self._say(self._stream_id(kwargs), "\n".join(lines))
        return True, "", 2

    @Command(
        name="wardrobe_help",
        pattern=r"^\s*/衣橱(?:帮助|help|菜单)\s*$",
        description="查看衣橱插件命令帮助",
    )
    @_safe_command
    async def cmd_help(self, **kwargs: Any) -> tuple[bool, str, int]:
        """帮助。"""

        if not await self._command_ready(kwargs):
            return True, "", 2
        text = (
            "🐟 她的衣橱 · 命令\n"
            "`/衣橱` 或 `/衣橱状态` 查看今天的穿搭\n"
            "`/衣橱列表` 查看所有衣服\n"
            "`/衣橱上传 <id> <名字>` 上传图片并新建/补充一套衣服（可带图、可回复图片）\n"
            "`/衣橱描述 <id>` 用视觉模型重新生成这套衣服的描述\n"
            "`/衣橱换装` 重新抽一套今天的衣服（管理员）\n"
            "`/衣橱事件` 查看今天和未来的特殊日子\n"
            "`/衣橱预览 <YYYY-MM-DD>` 预览某天会穿什么（管理员）\n"
            "`/看看你` 或 `/自拍` 发送今天的照片\n\n"
            "衣服的季节、权重、特殊日子在插件数据目录的 wardrobe.toml 里配置。"
        )
        await self._say(self._stream_id(kwargs), text)
        return True, "", 2

    @Command(
        name="wardrobe_upload",
        pattern=r"^\s*/衣橱(?:上传|upload)\b.*$",
        description="上传穿搭图片：/衣橱上传 <id> <名字>，可同时带图或回复一张图",
    )
    @_safe_command
    async def cmd_upload(self, **kwargs: Any) -> tuple[bool, str, int]:
        """上传图片。"""

        if not await self._command_ready(kwargs):
            return True, "", 2
        stream_id = self._stream_id(kwargs)
        if not self.config.upload.enabled:
            await self._say(stream_id, "图片上传功能已关闭。")
            return True, "", 2
        if not self._can_upload(kwargs):
            await self._say(
                stream_id,
                "你没有上传权限。请让管理员在 config.toml 的 [upload] 里把你的 QQ 号加入 "
                "allowed_user_ids，或把 allow_all 设为 true。",
            )
            return True, "", 2

        text = str(kwargs.get("text", "") or "").strip()
        match = re.match(r"^\s*/衣橱(?:上传|upload)\s*(.*)$", text, flags=re.IGNORECASE | re.DOTALL)
        rest = (match.group(1) if match else "").strip()
        if not rest:
            await self._say(
                stream_id,
                "用法：`/衣橱上传 <id> <名字>`\n"
                "例如：`/衣橱上传 spring_dress 春日白裙`\n"
                "把图片和这句话一起发，或者回复一张图片再发这句话。",
            )
            return True, "", 2

        parts = rest.split(maxsplit=1)
        raw_outfit_id = parts[0].strip()
        caption = parts[1].strip() if len(parts) > 1 else ""
        try:
            outfit_id = safe_outfit_id(raw_outfit_id)
        except ValueError as exc:
            await self._say(stream_id, f"衣服 ID 不合法：{exc}")
            return True, "", 2

        image_payloads = await self._collect_upload_images(kwargs)
        if not image_payloads:
            await self._say(
                stream_id,
                "没有找到图片。请把图片和 `/衣橱上传 ...` 一起发送，或回复一张图片再发送命令。",
            )
            return True, "", 2

        result = await self._save_outfit_images(outfit_id, "", caption, image_payloads)
        if not result.get("ok"):
            await self._say(stream_id, str(result.get("error") or result.get("message") or "上传失败"))
            return True, "", 2
        await self._say(stream_id, str(result.get("message") or "上传成功"))
        return True, "", 2

    async def _save_outfit_images(
        self,
        raw_outfit_id: str,
        name: str,
        caption: str,
        image_payloads: Sequence[bytes],
    ) -> dict[str, Any]:
        """保存一批图片到指定衣服，供 QQ 命令和网页上传共用。"""

        try:
            outfit_id = safe_outfit_id(raw_outfit_id)
        except ValueError as exc:
            return {"ok": False, "error": f"衣服 ID 不合法：{exc}"}
        if not image_payloads:
            return {"ok": False, "error": "没有收到图片数据"}

        wardrobe = self._wardrobe()
        caption = str(caption or "").strip()
        name = str(name or "").strip()
        if name:
            display_name = name[:24]
        elif caption:
            display_name = re.split(r"[，。,.；;]", caption, maxsplit=1)[0].strip()[:24]
        else:
            display_name = outfit_id
        outfit, created = wardrobe.ensure_outfit(outfit_id, name=display_name, description="")
        if caption and (created or self.config.upload.overwrite_description or not outfit.description):
            outfit.description = caption
        if created and not name and not caption:
            outfit.name = outfit_id

        saved_count = 0
        last_relative = ""
        max_bytes = int(self.config.upload.max_file_mb * 1024 * 1024)
        max_total_bytes = int(self.config.upload.max_total_mb * 1024 * 1024)
        for data in image_payloads[:_MAX_UPLOAD_IMAGES]:
            try:
                relative_path, _image_format = save_image(
                    self._data_dir or Path("."),
                    outfit_id,
                    data,
                    max_bytes=max_bytes,
                    max_total_bytes=max_total_bytes,
                )
            except ImageError as exc:
                if saved_count and self._store is not None:
                    self._store.save(wardrobe)
                return {"ok": False, "error": f"图片保存失败：{exc}", "outfit_id": outfit.id}
            if relative_path not in outfit.images:
                outfit.images.append(relative_path)
                saved_count += 1
            last_relative = relative_path

        kept, removed = prune_outfit_images(
            self._data_dir or Path("."),
            outfit.images,
            keep=max(1, int(self.config.upload.max_images_per_outfit)),
        )
        outfit.images = kept
        if self._store is not None:
            self._store.save(wardrobe)

        description = ""
        description_note = ""
        if self.config.upload.auto_describe and self.config.vision.enabled and last_relative:
            try:
                description = await self._describe_outfit_image(outfit, last_relative)
                if description:
                    outfit.vision_description = description
                    if not outfit.description:
                        outfit.description = description
                    if self._store is not None:
                        self._store.save(wardrobe)
                    description_note = f"视觉描述：{description}"
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("上传后生成视觉描述失败: %s", exc)
                description_note = f"视觉描述生成失败：{exc}，可以稍后用 `/衣橱描述 {outfit.id}` 重试。"

        lines = [
            f"已{'新建' if created else '更新'}「{outfit.display_name}」({outfit.id})，"
            f"本次保存 {saved_count} 张，当前共 {len(outfit.images)} 张。"
        ]
        if removed:
            lines.append(f"已按上限清理 {len(removed)} 张旧图片。")
        if description_note:
            lines.append(description_note)
        lines.append("想让它只在特定季节出现，可以在 wardrobe.toml 里设置 seasons。")
        return {
            "ok": True,
            "outfit_id": outfit.id,
            "outfit_name": outfit.display_name,
            "created": created,
            "saved": saved_count,
            "image_count": len(outfit.images),
            "removed": len(removed),
            "description": description,
            "message": "\n".join(lines),
        }

    @Command(
        name="wardrobe_describe",
        pattern=r"^\s*/衣橱(?:描述|describe)\b.*$",
        description="用视觉模型重新生成某套衣服的描述：/衣橱描述 <id>",
    )
    @_safe_command
    async def cmd_describe(self, **kwargs: Any) -> tuple[bool, str, int]:
        """重新生成描述。"""

        if not await self._command_ready(kwargs):
            return True, "", 2
        stream_id = self._stream_id(kwargs)
        if not self._is_admin(kwargs):
            await self._say(stream_id, "只有管理员可以重新生成描述。")
            return True, "", 2
        if not self.config.vision.enabled:
            await self._say(stream_id, "视觉描述功能已关闭。")
            return True, "", 2

        text = str(kwargs.get("text", "") or "").strip()
        match = re.match(r"^\s*/衣橱(?:描述|describe)\s*(.*)$", text, flags=re.IGNORECASE | re.DOTALL)
        raw_outfit_id = (match.group(1) if match else "").strip()
        wardrobe = self._wardrobe()
        outfit = wardrobe.get_outfit(raw_outfit_id) if raw_outfit_id else self._outfit_of(self._ensure_today(self._today()))
        if outfit is None:
            await self._say(stream_id, "找不到这套衣服。用法：`/衣橱描述 <id>`")
            return True, "", 2
        if not outfit.images:
            await self._say(stream_id, f"「{outfit.display_name}」还没有图片，先上传一张吧。")
            return True, "", 2

        relative_path = outfit.images[-1]
        try:
            description = await self._describe_outfit_image(outfit, relative_path)
        except Exception as exc:  # noqa: BLE001
            await self._say(stream_id, f"视觉描述生成失败：{exc}")
            return False, "", 2
        if not description:
            await self._say(stream_id, "视觉模型没有返回可用的描述。")
            return False, "", 2
        outfit.vision_description = description
        if self._store is not None:
            self._store.save(wardrobe)
        await self._say(stream_id, f"已更新「{outfit.display_name}」的描述：\n{description}")
        return True, "", 2

    @Command(
        name="wardrobe_reroll",
        pattern=r"^\s*/衣橱(?:换装|reroll)\s*$",
        description="重新抽一套今天的衣服（管理员）",
    )
    @_safe_command
    async def cmd_reroll(self, **kwargs: Any) -> tuple[bool, str, int]:
        """换一套。"""

        if not await self._command_ready(kwargs):
            return True, "", 2
        stream_id = self._stream_id(kwargs)
        if not self._is_admin(kwargs):
            await self._say(stream_id, "只有管理员可以换装。")
            return True, "", 2
        selection = self._reroll_today()
        if selection is None:
            await self._say(stream_id, "衣橱里还没有衣服，先上传一套吧。")
            return True, "", 2
        outfit = self._outfit_of(selection)
        await self._say(
            stream_id,
            f"换好了～今天穿「{outfit.display_name if outfit else selection.outfit_id}」"
            f"（{season_label(selection.season)}）。",
        )
        return True, "", 2

    @Command(
        name="wardrobe_events",
        pattern=r"^\s*/衣橱(?:事件|events)\s*$",
        description="查看今天和未来的特殊日子",
    )
    @_safe_command
    async def cmd_events(self, **kwargs: Any) -> tuple[bool, str, int]:
        """事件列表。"""

        if not await self._command_ready(kwargs):
            return True, "", 2
        stream_id = self._stream_id(kwargs)
        wardrobe = self._wardrobe()
        today = self._today()
        season_id = self._season_of(today)
        active = active_events(wardrobe.events, today, season_id)
        lines = ["📅 特殊日子"]
        if active:
            lines.append("今天：" + "、".join(event.name for event in active))
            for event in active:
                if event.outfits:
                    lines.append(f"  · {event.name} → {', '.join(event.outfits)}")
                if event.note:
                    lines.append(f"  · {event.note}")
        else:
            lines.append("今天：没有特别安排")

        horizon = max(0, int(self.config.event.lookahead_days))
        upcoming = upcoming_events(
            wardrobe.events,
            today,
            horizon,
            hemisphere=self.config.season.hemisphere,
            ranges=self.config.season.ranges,
        )
        future = [(day, event) for day, event in upcoming if day != today]
        if future:
            lines.append(f"\n未来 {horizon} 天：")
            for day, event in future[:20]:
                outfit_text = f" → {', '.join(event.outfits)}" if event.outfits else ""
                lines.append(f"  {day.isoformat()}（{weekday_label(day)}）{event.name}{outfit_text}")
        else:
            lines.append(f"\n未来 {horizon} 天没有登记的特殊日子。")

        if not has_lunar_support() and any(event.lunar_dates for event in wardrobe.events):
            lines.append("\n⚠️ 未安装可选依赖 lunardate，农历事件不会生效。")
        await self._say(stream_id, "\n".join(lines))
        return True, "", 2

    @Command(
        name="wardrobe_preview",
        pattern=r"^\s*/衣橱(?:预览|preview)\b.*$",
        description="预览某天会穿什么：/衣橱预览 <YYYY-MM-DD>（管理员）",
    )
    @_safe_command
    async def cmd_preview(self, **kwargs: Any) -> tuple[bool, str, int]:
        """预览指定日期。"""

        if not await self._command_ready(kwargs):
            return True, "", 2
        stream_id = self._stream_id(kwargs)
        if not self._is_admin(kwargs):
            await self._say(stream_id, "只有管理员可以预览。")
            return True, "", 2
        text = str(kwargs.get("text", "") or "").strip()
        match = re.match(r"^\s*/衣橱(?:预览|preview)\s*(.*)$", text, flags=re.IGNORECASE | re.DOTALL)
        raw_day = (match.group(1) if match else "").strip()
        target = parse_iso_date(raw_day)
        if target is None:
            await self._say(stream_id, "用法：`/衣橱预览 2026-10-28`")
            return True, "", 2
        selection = self._preview_day(target)
        if selection is None:
            await self._say(stream_id, "衣橱里还没有衣服。")
            return True, "", 2
        outfit = self._outfit_of(selection)
        events = self._active_events(target, selection)
        lines = [
            f"🔮 {target.isoformat()}（{weekday_label(target)}）",
            f"季节：{season_label(selection.season)}",
        ]
        if events:
            lines.append("特殊日子：" + "、".join(event.name for event in events))
        lines.append(f"穿搭：{outfit.display_name if outfit else selection.outfit_id}")
        if outfit is not None and outfit.prompt_description():
            lines.append(f"细节：{outfit.prompt_description()}")
        lines.append(f"来源：{outcome_source_label(selection.source)}")
        await self._say(stream_id, "\n".join(lines))
        return True, "", 2

    @Command(
        name="wardrobe_selfie",
        pattern=r"^\s*/(?:看看你|自拍|衣橱自拍)\s*$",
        description="看看麦麦今天长什么样、穿了什么",
    )
    @_safe_command
    async def cmd_selfie(self, **kwargs: Any) -> tuple[bool, str, int]:
        """发今天的照片。"""

        if not await self._command_ready(kwargs):
            return True, "", 2
        stream_id = self._stream_id(kwargs)
        ok, message = await self._send_selfie(stream_id)
        if not ok:
            await self._say(stream_id, message)
        return True, "", 2

    # ------------------------------------------------------------------ 命令辅助

    async def _command_ready(self, kwargs: Mapping[str, Any]) -> bool:
        """命令通用前置检查：开关、群聊/私聊白名单。"""

        if not self.config.commands.enabled:
            return False
        message = kwargs.get("message")
        is_group = False
        if isinstance(message, dict):
            info = message.get("message_info")
            if isinstance(info, dict):
                is_group = bool(info.get("group_info"))
        if is_group and not self.config.commands.allowed_in_groups:
            return False
        if not is_group and not self.config.commands.allowed_in_private:
            return False
        if not self.config.plugin.enabled:
            stream_id = self._stream_id(kwargs)
            if stream_id:
                await self._say(stream_id, "「她的衣橱」当前没有启用。")
            return False
        return True

    def _stream_id(self, kwargs: Mapping[str, Any]) -> str:
        return str(kwargs.get("stream_id", "") or "").strip()

    def _is_admin(self, kwargs: Mapping[str, Any]) -> bool:
        if bool(kwargs.get("is_local_operator")):
            return True
        user_id = self._user_id(kwargs)
        admin_ids = {str(item).strip() for item in self.config.commands.admin_user_ids if str(item).strip()}
        return bool(user_id and user_id in admin_ids)

    def _can_upload(self, kwargs: Mapping[str, Any]) -> bool:
        if self.config.upload.allow_all:
            return True
        if self._is_admin(kwargs):
            return True
        user_id = self._user_id(kwargs)
        allowed = {str(item).strip() for item in self.config.upload.allowed_user_ids if str(item).strip()}
        return bool(user_id and user_id in allowed)

    @staticmethod
    def _user_id(kwargs: Mapping[str, Any]) -> str:
        user_id = str(kwargs.get("user_id", "") or "").strip()
        if user_id:
            return user_id
        message = kwargs.get("message")
        if isinstance(message, dict):
            info = message.get("message_info")
            if isinstance(info, dict):
                user_info = info.get("user_info")
                if isinstance(user_info, dict):
                    return str(user_info.get("user_id", "") or "").strip()
        return ""

    async def _say(self, stream_id: str, text: str) -> bool:
        """发送文本，自动截断并吞掉异常。"""

        normalized = str(text or "").strip()
        if not stream_id or not normalized:
            return False
        if len(normalized) > _MAX_MESSAGE_CHARS:
            normalized = normalized[:_MAX_MESSAGE_CHARS].rstrip() + "…"
        try:
            await self.ctx.send.text(text=normalized, stream_id=stream_id)
            return True
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("衣橱消息发送失败: %s", exc)
            return False

    async def _collect_upload_images(self, kwargs: Mapping[str, Any]) -> list[bytes]:
        """从当前消息或回复消息里收集图片二进制。"""

        message = kwargs.get("message")
        message_ids: list[str] = []
        if isinstance(message, dict):
            message_id = str(message.get("message_id", "") or "").strip()
            if message_id:
                message_ids.append(message_id)
            reply_target = self._reply_target(message)
            if reply_target and reply_target not in message_ids:
                message_ids.insert(0, reply_target)

        images: list[bytes] = []
        stream_id = self._stream_id(kwargs)
        for message_id in message_ids:
            fetched: Any = None
            try:
                fetched = await self.ctx.message.get_by_id(
                    message_id,
                    chat_id=stream_id,
                    include_binary_data=True,
                )
                if not isinstance(fetched, dict) and stream_id:
                    fetched = await self.ctx.message.get_by_id(
                        message_id,
                        include_binary_data=True,
                    )
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.debug("读取消息 %s 失败: %s", message_id, exc)
                continue
            if not isinstance(fetched, dict):
                continue
            for segment in fetched.get("raw_message") or []:
                if not isinstance(segment, dict) or segment.get("type") != "image":
                    continue
                raw_base64 = str(segment.get("binary_data_base64", "") or "")
                if not raw_base64:
                    continue
                try:
                    data = base64.b64decode(raw_base64, validate=False)
                except Exception:  # noqa: BLE001
                    continue
                if data:
                    images.append(data)
                    if len(images) >= _MAX_UPLOAD_IMAGES:
                        return images
        return images

    @staticmethod
    def _reply_target(message: Mapping[str, Any]) -> str:
        for segment in message.get("raw_message") or []:
            if not isinstance(segment, dict) or segment.get("type") != "reply":
                continue
            data = segment.get("data")
            if isinstance(data, dict):
                target = str(data.get("target_message_id", "") or "").strip()
                if target:
                    return target
            elif isinstance(data, str) and data.strip():
                return data.strip()
        return str(message.get("reply_to", "") or "").strip()

    # ------------------------------------------------------------------ 自拍

    async def _send_selfie(self, stream_id: str, *, outfit_id: str = "", via_tool: bool = False) -> tuple[bool, str]:
        """发送今日穿搭图；没有图片时退化为文字描述。"""

        if not self.config.selfie.enabled:
            return False, "自拍功能当前没有开启。"
        now = time.monotonic()
        last_sent = self._last_selfie_at.get(stream_id, 0.0)
        cooldown = max(0, int(self.config.selfie.cooldown_seconds))
        if cooldown and now - last_sent < cooldown:
            remaining = int(cooldown - (now - last_sent)) + 1
            return False, f"刚刚才发过照片，{remaining} 秒后再来吧～"

        today = self._today()
        selection = self._ensure_today(today)
        outfit: Outfit | None = None
        if outfit_id:
            outfit = self._wardrobe().get_outfit(outfit_id)
        if outfit is None and selection is not None:
            outfit = self._outfit_of(selection)

        if outfit is None:
            return False, "今天还没有衣服可以穿，先用 `/衣橱上传` 加一套吧。"
        if not outfit.images:
            description = outfit.prompt_description()
            if description:
                text = f"{self._display_name or '我'}今天穿的是「{outfit.display_name}」：{description}"
            else:
                text = f"今天穿的是「{outfit.display_name}」，不过还没有照片～"
            await self._say(stream_id, text)
            self._last_selfie_at[stream_id] = now
            return True, "已经把穿搭描述发过去了。"

        relative_path = random.choice(list(outfit.images))
        loaded = load_image_base64(self._data_dir or Path("."), relative_path)
        if loaded is None:
            return False, "图片文件读取失败，可能已经被删除了。"
        _image_format, image_base64 = loaded
        caption = build_selfie_caption(
            self.config.selfie.caption,
            name=self._display_name,
            outfit=outfit,
            selection=selection,
            event_names=[event.name for event in self._active_events(today, selection)],
        )
        if caption:
            await self._say(stream_id, caption)
        try:
            sent = await self.ctx.send.image(image_data=image_base64, stream_id=stream_id)
        except Exception as exc:  # noqa: BLE001
            return False, f"图片发送失败：{exc}"
        if sent is False:
            return False, "图片发送失败。"
        self._last_selfie_at[stream_id] = now
        self.ctx.logger.info("已发送自拍：%s（%s）", outfit.id, "tool" if via_tool else "command")
        return True, "照片已经发出去了。"

    def _active_stream_id(self) -> str:
        ttl = max(30, int(self.config.selfie.session_ttl_seconds))
        if self._last_session_id and time.monotonic() - self._last_session_at <= ttl:
            return self._last_session_id
        return ""

    def _remember_session(self, session_id: str) -> None:
        self._last_session_id = session_id
        self._last_session_at = time.monotonic()

    # ------------------------------------------------------------------ 视觉描述

    async def _describe_outfit_image(self, outfit: Outfit, relative_path: str) -> str:
        """调用视觉模型描述一张穿搭图。"""

        if not self.config.vision.enabled:
            raise RuntimeError("视觉描述功能已关闭")
        loaded = load_image_for_vision(
            self._data_dir or Path("."),
            relative_path,
            max_side=int(self.config.vision.max_image_side),
            jpeg_quality=int(self.config.vision.jpeg_quality),
        )
        if loaded is None:
            raise RuntimeError("图片文件读取失败")
        image_format, image_base64 = loaded
        prompt = [
            {
                "role": "system",
                "content": "你是一个严谨的服装与人物外观描述助手，只描述图片中真实存在的内容。",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": self.config.vision.prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/{image_format};base64,{image_base64}"},
                    },
                ],
            },
        ]
        result = await self.ctx.llm.generate(
            prompt,
            task_name=self.config.vision.task_name,
            temperature=float(self.config.vision.temperature),
            max_tokens=int(self.config.vision.max_tokens),
        )
        if not isinstance(result, dict) or not result.get("success"):
            error = result.get("error") if isinstance(result, dict) else "未知错误"
            raise RuntimeError(str(error or "视觉模型调用失败"))
        text = str(result.get("response") or result.get("content") or "").strip()
        if not text:
            raise RuntimeError("视觉模型没有返回正文（可能 token 被推理内容用完），请调大 [vision].max_tokens")
        text = re.sub(r"^[#*\-\s]+", "", text).strip()
        text = re.sub(r"^(?:图中|图片中|画面中)(?:的)?(?:这位)?(?:女生|女子|女孩|角色)?[，,：:\s]*", "", text)
        text = text.replace("该角色", "她").replace("该女子", "她")
        text = re.sub(r"\s+", " ", text)
        max_chars = max(40, int(self.config.vision.description_max_chars))
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        return text

    # ------------------------------------------------------------------ 网页上传

    def _ensure_web_socket(self) -> socket.socket | None:
        """预留一个只监听 127.0.0.1 的端口，供首页卡片和上传页使用。"""

        if not self.config.web_ui.enabled:
            return None
        if self._web_socket is not None:
            return self._web_socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", int(self.config.web_ui.port)))
            sock.listen(32)
            sock.setblocking(False)
            self._web_port = int(sock.getsockname()[1])
        except OSError as exc:
            sock.close()
            self.ctx.logger.warning("衣橱网页上传端口不可用: %s", exc)
            return None
        self._web_socket = sock
        return sock

    def _web_url(self) -> str:
        return f"http://127.0.0.1:{self._web_port}/?token={self._web_token}"

    async def _start_web_server(self) -> None:
        if not self.config.web_ui.enabled:
            return
        sock = self._ensure_web_socket()
        if sock is None:
            return
        if self._web_server is None:
            self._web_server = WardrobeWebServer(self)
        if await self._web_server.start(sock):
            self.ctx.logger.info(
                "衣橱网页上传已启动：http://127.0.0.1:%s/（入口：MaiBot 首页「她的衣橱」卡片）",
                self._web_port,
            )
        else:
            self.ctx.logger.warning("衣橱网页上传服务未能启动，QQ 命令仍然可用")

    async def _stop_web_server(self) -> None:
        if self._web_server is not None:
            await self._web_server.stop()
            self._web_server = None
        if self._web_socket is not None:
            try:
                self._web_socket.close()
            except Exception:  # noqa: BLE001
                pass
            self._web_socket = None
        self._web_port = 0

    # ------------------------------------------------------------------ 数据

    def _resolve_data_dir(self) -> Path:
        try:
            return Path(self.ctx.paths.data_dir)
        except Exception:  # noqa: BLE001
            return Path("data") / "plugins" / self.ctx.plugin_id

    def _data_file(self, relative_name: str, fallback: str) -> Path:
        """把配置里的文件名安全地拼到数据目录下，拒绝绝对路径和 ``..``。"""

        base = self._data_dir or Path("data")
        name = str(relative_name or "").replace("\\", "/").strip()
        candidate = Path(name)
        if not name or candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            return base / fallback
        return base / candidate

    async def _resolve_display_name(self) -> None:
        configured = str(self.config.identity.display_name or "").strip()
        if configured:
            self._display_name = configured
            return
        nickname = ""
        try:
            value = await self.ctx.config.get("bot.nickname", "")
            nickname = str(value or "").strip()
        except Exception:  # noqa: BLE001
            nickname = ""
        self._display_name = nickname or "麦麦"

    def _wardrobe(self) -> Wardrobe:
        if self._store is None:
            return Wardrobe()
        return self._store.ensure_loaded()

    def _today(self) -> date:
        simulated = parse_iso_date(str(self.config.debug.simulated_date or ""))
        if simulated is not None:
            return simulated
        return datetime.now(self._timezone()).date()

    def _timezone(self) -> ZoneInfo | None:
        name = str(self.config.plugin.timezone or "").strip()
        if not name:
            return None
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return None

    def _season_of(self, day: date) -> str:
        return season_of(
            day,
            hemisphere=self.config.season.hemisphere,
            ranges=self.config.season.ranges if self.config.season.enabled else None,
        )

    def _pick(self, day: date) -> Selection | None:
        wardrobe = self._wardrobe()
        state = self._state
        avoid_ids = state.recent_outfit_ids(day, self.config.rotation.avoid_repeat_days) if state else set()
        return select_outfit(
            wardrobe,
            day,
            season_id=self._season_of(day),
            events_enabled=self.config.event.enabled,
            rotation_enabled=self.config.rotation.enabled,
            avoid_ids=avoid_ids,
            avoid_repeat_days=self.config.rotation.avoid_repeat_days,
            prefer_season=self.config.rotation.prefer_season,
            fallback_to_all=self.config.rotation.fallback_to_all,
            min_pool_size=self.config.rotation.min_pool_size,
            seed=self.config.rotation.seed,
            hemisphere=self.config.season.hemisphere,
            season_ranges=self.config.season.ranges,
        )

    def _ensure_today(self, day: date) -> Selection | None:
        if self._state is None:
            return None
        selection = self._state.ensure_today(day, lambda: self._pick(day))
        if selection is not None and self._wardrobe().get_outfit(selection.outfit_id) is None:
            selection = self._state.reroll(day, lambda: self._pick(day))
        return selection

    def _reroll_today(self) -> Selection | None:
        if self._state is None:
            return None
        return self._state.reroll(self._today(), lambda: self._pick(self._today()))

    def _preview_day(self, day: date) -> Selection | None:
        wardrobe = self._wardrobe()
        state = self._state
        avoid_ids = state.recent_outfit_ids(day, self.config.rotation.avoid_repeat_days) if state else set()
        return select_outfit(
            wardrobe,
            day,
            season_id=self._season_of(day),
            events_enabled=self.config.event.enabled,
            rotation_enabled=self.config.rotation.enabled,
            avoid_ids=avoid_ids,
            avoid_repeat_days=self.config.rotation.avoid_repeat_days,
            prefer_season=self.config.rotation.prefer_season,
            fallback_to_all=self.config.rotation.fallback_to_all,
            min_pool_size=self.config.rotation.min_pool_size,
            seed=self.config.rotation.seed,
            hemisphere=self.config.season.hemisphere,
            season_ranges=self.config.season.ranges,
        )

    def _outfit_of(self, selection: Selection | None) -> Outfit | None:
        if selection is None:
            return None
        return self._wardrobe().get_outfit(selection.outfit_id)

    def _active_events(self, day: date, selection: Selection | None = None) -> list[Any]:
        if not self.config.event.enabled:
            return []
        wardrobe = self._wardrobe()
        season_id = selection.season if selection is not None else self._season_of(day)
        return active_events(wardrobe.events, day, season_id)


def create_plugin() -> WardrobePlugin:
    """插件入口。"""

    return WardrobePlugin()
