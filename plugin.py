"""「她的衣橱」简化版：上传图片识别穿搭、每天随机换、事件指定衣服。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import re
import secrets
import shutil
import socket
import time
import uuid

from maibot_sdk import Command, HookHandler, MaiBotPlugin
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

from .calendar_rules import active_events, is_valid_date_expression
from .config import WardrobeConfig
from .images import ImageError, delete_image, load_image_for_vision, prune_outfit_images, save_image
from .prompt import build_identity_prompt, weekday_label
from .state import DailyState
from .wardrobe import Outfit, Selection, Wardrobe, WardrobeEvent, WardrobeStore, safe_outfit_id, select_outfit
from .webui import WardrobeWebServer

_PROMPT_MARKER = "【你此刻的形象"
_MAX_MESSAGE_CHARS = 1800


class WardrobePlugin(MaiBotPlugin):
    """她的衣橱。"""

    config_model = WardrobeConfig

    def __init__(self) -> None:
        super().__init__()
        self._data_dir: Path | None = None
        self._store: WardrobeStore | None = None
        self._state: DailyState | None = None
        self._display_name: str = ""
        self._web_socket: socket.socket | None = None
        self._web_server: WardrobeWebServer | None = None
        self._web_port: int = 0
        self._web_token: str = secrets.token_urlsafe(24)

    # ------------------------------------------------------------------ 生命周期

    async def on_load(self) -> None:
        """加载衣橱并启动网页。"""

        try:
            self._data_dir = self._resolve_data_dir()
            self._data_dir.mkdir(parents=True, exist_ok=True)
            package_dir = Path(__file__).resolve().parent
            self._store = WardrobeStore(
                self._data_dir / "wardrobe.toml",
                template_path=package_dir / "assets" / "default_wardrobe.toml",
                logger=self.ctx.logger,
            )
            self._store.ensure_loaded(force=True)
            self._state = DailyState.load(self._data_dir / "state.json")
            await self._resolve_display_name()
            if not self.config.plugin.enabled:
                self.ctx.logger.info("「她的衣橱」已加载，但插件未启用")
                return

            await self._start_web_server()
            selection = self._ensure_today(self._today())
            self.ctx.logger.info(
                "「她的衣橱」已加载：%d 套衣服，%d 个事件；今日=%s",
                len(self._store.wardrobe.outfits),
                len(self._store.wardrobe.events),
                selection.outfit_id if selection else "（空）",
            )
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.exception("「她的衣橱」初始化失败: %s", exc)

    async def on_unload(self) -> None:
        """关闭网页并保存状态。"""

        try:
            await self._stop_web_server()
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("衣橱网页关闭失败: %s", exc)
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
            wardrobe_path = self._data_dir / "wardrobe.toml"
            state_path = self._data_dir / "state.json"
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

    # ------------------------------------------------------------------ 首页卡片

    def get_components(self) -> list[dict[str, Any]]:
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
                        "description": "上传图片识别穿搭、每天随机换一套、自己写特殊事件。",
                        "content": "不用在 QQ 里发指令；点开就能上传、换装和编辑事件。",
                        "link_url": self._web_url(),
                        "link_label": "打开衣橱",
                        "icon": "shirt",
                        "width": "medium",
                        "order": int(self.config.web_ui.home_card_order),
                        "show_title": True,
                    },
                }
            )
        except RuntimeError:
            return components
        except Exception as exc:  # noqa: BLE001
            self._get_logger().warning("衣橱首页卡片注册失败: %s", exc)
        return components

    # ------------------------------------------------------------------ 提示词注入

    @HookHandler(
        "maisaka.planner.before_request",
        name="wardrobe_planner_inject",
        description="把麦麦今日穿搭注入 planner 上下文",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        timeout_ms=6000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_planner_before_request(self, **kwargs: Any) -> dict[str, Any]:
        return self._inject_identity(kwargs)

    @HookHandler(
        "maisaka.replyer.before_model_request",
        name="wardrobe_replyer_inject",
        description="把麦麦今日穿搭注入 replyer 请求",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        timeout_ms=6000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_replyer_before_model_request(self, **kwargs: Any) -> dict[str, Any]:
        return self._inject_identity(kwargs)

    def _inject_identity(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        unchanged = {"action": "continue"}
        try:
            if not self.config.plugin.enabled or not self.config.injection.enabled:
                return unchanged
            items = kwargs.get("items")
            if not isinstance(items, list) or not items:
                return unchanged
            block = self._build_prompt_block()
            if not block or self._has_prompt_marker(items):
                return unchanged
            self._append_system_text(items, block)
            return {"action": "continue", "modified_kwargs": kwargs}
        except Exception as exc:  # noqa: BLE001
            self._get_logger().warning("衣橱提示词注入失败: %s", exc)
            return unchanged

    @staticmethod
    def _has_prompt_marker(items: Sequence[Any]) -> bool:
        for item in items:
            if not isinstance(item, dict) or item.get("item_type") != "SystemMessageItem":
                continue
            for part in item.get("parts") or []:
                if isinstance(part, dict) and part.get("type") == "text" and _PROMPT_MARKER in str(part.get("text", "")):
                    return True
        return False

    @staticmethod
    def _append_system_text(items: list[Any], text: str) -> None:
        for item in items:
            if isinstance(item, dict) and item.get("item_type") == "SystemMessageItem":
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
        today = self._today()
        selection = self._ensure_today(today)
        outfit = self._outfit_of(selection)
        events = self._active_events(today)
        return build_identity_prompt(
            display_name=self._display_name or self.config.identity.display_name,
            base_appearance=self.config.identity.base_appearance,
            extra_traits=self.config.identity.extra_traits,
            instruction=self.config.identity.instruction,
            selection=selection,
            outfit=outfit,
            day=today,
            event_names=[event.name for event in events],
            max_chars=self.config.injection.max_chars,
        )

    # ------------------------------------------------------------------ 命令

    @Command(
        name="wardrobe_status",
        pattern=r"^\s*/衣橱(?:状态|status)?\s*$",
        description="查看麦麦今天的穿搭",
    )
    async def cmd_status(self, **kwargs: Any) -> tuple[bool, str, int]:
        stream_id = str(kwargs.get("stream_id", "") or "").strip()
        if not self.config.plugin.enabled:
            await self._say(stream_id, "「她的衣橱」当前没有启用。")
            return True, "", 2
        today = self._today()
        selection = self._ensure_today(today)
        wardrobe = self._wardrobe()
        if selection is None or not wardrobe.outfits:
            await self._say(stream_id, "衣橱里还没有衣服。打开 MaiBot 首页的「她的衣橱」卡片上传一套吧。")
            return True, "", 2
        outfit = self._outfit_of(selection)
        events = self._active_events(today)
        lines = [f"🐟 今日穿搭 · {today.isoformat()}（{weekday_label(today)}）"]
        if events:
            lines.append("事件：" + "、".join(event.name for event in events))
            if selection.note:
                lines.append(f"说明：{selection.note}")
        lines.append(f"穿搭：{outfit.display_name if outfit else selection.outfit_id}")
        if outfit is not None and outfit.prompt_description():
            lines.append(f"细节：{outfit.prompt_description()}")
        if outfit is not None:
            lines.append(f"图片：{len(outfit.images)} 张")
        lines.append(f"衣橱：{len(wardrobe.outfits)} 套")
        await self._say(stream_id, "\n".join(lines))
        return True, "", 2

    # ------------------------------------------------------------------ 网页

    def _ensure_web_socket(self) -> socket.socket | None:
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
            self.ctx.logger.warning("衣橱网页端口不可用: %s", exc)
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
                "衣橱网页已启动：http://127.0.0.1:%s/（入口：MaiBot 首页「她的衣橱」卡片）", self._web_port
            )
        else:
            self.ctx.logger.warning("衣橱网页未能启动，/衣橱 命令仍然可用")

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

    # ------------------------------------------------------------------ 数据操作（网页与命令共用）

    async def _save_outfit_images(
        self,
        raw_outfit_id: str,
        name: str,
        description: str,
        image_payloads: Sequence[bytes],
    ) -> dict[str, Any]:
        try:
            outfit_id = safe_outfit_id(raw_outfit_id)
        except ValueError as exc:
            return {"ok": False, "error": f"衣服 ID 不合法：{exc}"}
        if not image_payloads:
            return {"ok": False, "error": "没有收到图片数据"}

        wardrobe = self._wardrobe()
        description = str(description or "").strip()
        display_name = str(name or "").strip()[:24] or outfit_id
        outfit, created = wardrobe.ensure_outfit(outfit_id, name=display_name, description=description)
        if created and description:
            outfit.description = description
        elif description:
            outfit.description = description

        saved = 0
        last_relative = ""
        max_bytes = int(self.config.upload.max_file_mb * 1024 * 1024)
        max_total_bytes = int(self.config.upload.max_total_mb * 1024 * 1024)
        for data in image_payloads:
            try:
                relative_path, _fmt = save_image(
                    self._data_dir or Path("."),
                    outfit_id,
                    data,
                    max_bytes=max_bytes,
                    max_total_bytes=max_total_bytes,
                )
            except ImageError as exc:
                if saved and self._store is not None:
                    self._store.save(wardrobe)
                return {"ok": False, "error": f"图片保存失败：{exc}", "outfit_id": outfit.id}
            if relative_path not in outfit.images:
                outfit.images.append(relative_path)
                saved += 1
            last_relative = relative_path

        kept, removed = prune_outfit_images(
            self._data_dir or Path("."),
            outfit.images,
            keep=max(1, int(self.config.upload.max_images_per_outfit)),
        )
        outfit.images = kept
        if self._store is not None:
            self._store.save(wardrobe)

        vision_note = ""
        if self.config.upload.auto_describe and self.config.vision.enabled and last_relative:
            try:
                vision = await self._describe_outfit_image(outfit, last_relative)
                if vision:
                    outfit.vision_description = vision
                    if not outfit.description:
                        outfit.description = vision
                    if self._store is not None:
                        self._store.save(wardrobe)
                    vision_note = f"\n识别结果：{vision}"
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("上传后视觉识别失败: %s", exc)
                vision_note = f"\n视觉识别失败：{exc}"

        lines = [
            f"已{'新建' if created else '更新'}「{outfit.display_name}」({outfit.id})，"
            f"本次保存 {saved} 张，当前共 {len(outfit.images)} 张。"
        ]
        if removed:
            lines.append(f"已按上限清理 {len(removed)} 张旧图。")
        if vision_note:
            lines.append(vision_note.strip())
        return {
            "ok": True,
            "outfit_id": outfit.id,
            "outfit_name": outfit.display_name,
            "created": created,
            "saved": saved,
            "image_count": len(outfit.images),
            "description": outfit.description,
            "vision_description": outfit.vision_description,
            "message": "\n".join(lines),
        }

    def _delete_outfit_image(self, outfit_id: str, index: int) -> dict[str, Any]:
        wardrobe = self._wardrobe()
        outfit = wardrobe.get_outfit(str(outfit_id or "").strip())
        if outfit is None:
            return {"ok": False, "error": "找不到这套衣服"}
        if index < 0 or index >= len(outfit.images):
            return {"ok": False, "error": "图片不存在"}
        relative_path = outfit.images.pop(index)
        delete_image(self._data_dir or Path("."), relative_path)
        if self._store is not None:
            self._store.save(wardrobe)
        return {"ok": True, "outfit_id": outfit.id, "image_count": len(outfit.images)}

    def _delete_outfit(self, outfit_id: str) -> dict[str, Any]:
        """删除整套衣服、它的图片目录，以及事件里对它的引用。"""

        normalized = str(outfit_id or "").strip()
        wardrobe = self._wardrobe()
        outfit = wardrobe.get_outfit(normalized)
        if outfit is None:
            return {"ok": False, "error": "找不到这套衣服"}

        wardrobe.remove_outfit(normalized)
        for event in wardrobe.events:
            event.outfits = [item for item in event.outfits if item != normalized]
        if self._store is not None:
            self._store.save(wardrobe)

        root = (self._data_dir or Path(".")) / "outfits"
        target = root / normalized
        try:
            resolved_root = root.resolve()
            resolved_target = target.resolve()
            if resolved_target != resolved_root and resolved_root in resolved_target.parents:
                shutil.rmtree(resolved_target, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("删除衣服图片目录失败: %s", exc)

        if self._state is not None and self._state.today is not None and self._state.today.outfit_id == normalized:
            self._state.clear_today()
            self._ensure_today(self._today())

        return {"ok": True, "outfit_id": normalized}

    def _save_event(
        self,
        event_id: str,
        name: str,
        date_text: str,
        outfit_ids: Sequence[str],
        note: str,
    ) -> dict[str, Any]:
        name = str(name or "").strip()
        date_text = str(date_text or "").strip()
        note = str(note or "").strip()
        if not name:
            return {"ok": False, "error": "事件名称不能为空"}
        if not date_text:
            return {"ok": False, "error": "事件日期不能为空"}
        if not is_valid_date_expression(date_text):
            return {"ok": False, "error": "日期格式不正确，例如 10-28、2026-10-28、12-24..12-26"}
        wardrobe = self._wardrobe()
        valid_outfits = []
        for outfit_id in outfit_ids:
            outfit = wardrobe.get_outfit(str(outfit_id or "").strip())
            if outfit is not None:
                valid_outfits.append(outfit.id)
        if not valid_outfits:
            return {"ok": False, "error": "至少选择一套衣服"}
        existing = wardrobe.get_event(event_id) if event_id else None
        if existing is None:
            existing = WardrobeEvent(id=f"event_{uuid.uuid4().hex[:8]}")
            wardrobe.events.append(existing)
        existing.name = name
        existing.date = date_text
        existing.outfits = valid_outfits
        existing.note = note
        existing.enabled = True
        if self._store is not None:
            self._store.save(wardrobe)
        return {"ok": True, "event": existing.to_mapping()}

    def _delete_event(self, event_id: str) -> dict[str, Any]:
        wardrobe = self._wardrobe()
        before = len(wardrobe.events)
        wardrobe.events = [event for event in wardrobe.events if event.id != str(event_id or "").strip()]
        if len(wardrobe.events) == before:
            return {"ok": False, "error": "事件不存在"}
        if self._store is not None:
            self._store.save(wardrobe)
        return {"ok": True}

    @staticmethod
    def _valid_date_expression(text: str) -> bool:
        return is_valid_date_expression(text)

    def _wear_today(self, outfit_id: str) -> Selection | None:
        wardrobe = self._wardrobe()
        outfit = wardrobe.get_outfit(str(outfit_id or "").strip())
        if outfit is None:
            return None
        today = self._today()
        events = self._active_events(today)
        selection = Selection(
            day=today.isoformat(),
            outfit_id=outfit.id,
            source="manual",
            event_ids=[event.id for event in events],
            note="；".join(event.note for event in events if event.note),
            picked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        if self._state is not None:
            self._state.set_today(selection)
        return selection

    def _reroll_today(self) -> Selection | None:
        if self._state is None:
            return None
        return self._state.reroll(self._today(), lambda: self._pick(self._today()))

    async def _describe_outfit_image(self, outfit: Outfit, relative_path: str) -> str:
        if not self.config.vision.enabled:
            raise RuntimeError("视觉识别功能已关闭")
        loaded = load_image_for_vision(self._data_dir or Path("."), relative_path)
        if loaded is None:
            raise RuntimeError("图片文件读取失败")
        image_format, image_base64 = loaded
        prompt = [
            {"role": "system", "content": "你是一个严谨的服装与人物外观描述助手，只描述图片中真实存在的内容。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": self.config.vision.prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/{image_format};base64,{image_base64}"}},
                ],
            },
        ]
        result = await self.ctx.llm.generate(
            prompt,
            task_name=self.config.vision.task_name,
            temperature=0.3,
            max_tokens=int(self.config.vision.max_tokens),
        )
        if not isinstance(result, dict) or not result.get("success"):
            error = result.get("error") if isinstance(result, dict) else "未知错误"
            raise RuntimeError(str(error or "视觉模型调用失败"))
        text = str(result.get("response") or result.get("content") or "").strip()
        if not text:
            raise RuntimeError("视觉模型没有返回正文，请调大 [vision].max_tokens")
        text = re.sub(r"^[#*\-\s]+", "", text).strip()
        text = re.sub(r"^(?:图中|图片中|画面中)(?:的)?(?:这位)?(?:女生|女子|女孩|角色)?[，,：:\s]*", "", text)
        text = text.replace("该角色", "她").replace("该女子", "她")
        text = re.sub(r"\s+", " ", text)
        max_chars = max(40, int(self.config.vision.description_max_chars))
        return text[:max_chars].rstrip() + "…" if len(text) > max_chars else text

    # ------------------------------------------------------------------ 数据

    def _resolve_data_dir(self) -> Path:
        try:
            return Path(self.ctx.paths.data_dir)
        except Exception:  # noqa: BLE001
            return Path("data") / "plugins" / self.ctx.plugin_id

    async def _resolve_display_name(self) -> None:
        configured = str(self.config.identity.display_name or "").strip()
        if configured:
            self._display_name = configured
            return
        try:
            nickname = str(await self.ctx.config.get("bot.nickname", "") or "").strip()
        except Exception:  # noqa: BLE001
            nickname = ""
        self._display_name = nickname or "麦麦"

    def _wardrobe(self) -> Wardrobe:
        if self._store is None:
            return Wardrobe()
        return self._store.ensure_loaded()

    def _today(self) -> date:
        return datetime.now(self._timezone()).date()

    def _timezone(self) -> ZoneInfo | None:
        name = str(self.config.plugin.timezone or "").strip()
        if not name:
            return None
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return None

    def _pick(self, day: date) -> Selection | None:
        wardrobe = self._wardrobe()
        avoid = self._state.recent_outfit_ids(day, self.config.rotation.avoid_repeat_days) if self._state else set()
        return select_outfit(
            wardrobe,
            day,
            avoid_ids=avoid,
            avoid_repeat_days=self.config.rotation.avoid_repeat_days,
            seed="maibot-wardrobe",
        )

    def _ensure_today(self, day: date) -> Selection | None:
        if self._state is None:
            return None
        selection = self._state.ensure_today(day, lambda: self._pick(day))
        if selection is not None and self._wardrobe().get_outfit(selection.outfit_id) is None:
            selection = self._state.reroll(day, lambda: self._pick(day))
            if selection is None:
                self._state.clear_today()
        return selection

    def _outfit_of(self, selection: Selection | None) -> Outfit | None:
        if selection is None:
            return None
        return self._wardrobe().get_outfit(selection.outfit_id)

    def _active_events(self, day: date) -> list[WardrobeEvent]:
        return active_events(self._wardrobe().events, day)

    async def _say(self, stream_id: str, text: str) -> bool:
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


def create_plugin() -> WardrobePlugin:
    """插件入口。"""

    return WardrobePlugin()
