"""插件自带的本地网页：上传图片、换装、编辑/删除事件。

只监听 ``127.0.0.1``，页面链接带一次性令牌；不经过 QQ。
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Mapping
from urllib.parse import parse_qs

import asyncio
import base64
import html
import json
import secrets

from .images import detect_image_format, load_image_base64, mime_type_for_format

_MAX_HEADER_BYTES = 32 * 1024
_MAX_UPLOAD_HARD_BYTES = 64 * 1024 * 1024


class WardrobeWebServer:
    """插件内置的 HTTP 页面。"""

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin
        self._server: asyncio.AbstractServer | None = None
        self._socket: Any = None

    @property
    def token(self) -> str:
        return str(getattr(self._plugin, "_web_token", "") or "")

    async def start(self, sock: Any) -> bool:
        if self._server is not None:
            return True
        try:
            self._socket = sock
            self._server = await asyncio.start_server(self._handle_connection, sock=sock)
            return True
        except Exception as exc:  # noqa: BLE001
            self._server = None
            self._plugin.ctx.logger.warning("衣橱网页启动失败: %s", exc)
            return False

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            self._server = None
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ 请求

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, target, headers = await self._read_request(reader)
            if method is None:
                await self._send(writer, HTTPStatus.BAD_REQUEST, b"bad request")
                return
            path, _, query = target.partition("?")
            params = parse_qs(query, keep_blank_values=True, max_num_fields=60)
            if not self._check_token(params, headers):
                await self._send(
                    writer,
                    HTTPStatus.FORBIDDEN,
                    "链接已失效。请回到 MaiBot 首页，点击「她的衣橱」卡片重新打开。".encode("utf-8"),
                )
                return

            if method in {"GET", "HEAD"} and path in {"/", "/index.html"}:
                await self._send(writer, HTTPStatus.OK, self._page_html().encode("utf-8"), "text/html; charset=utf-8")
                return
            if method == "GET" and path == "/api/state":
                await self._send_json(writer, HTTPStatus.OK, self._state_payload())
                return
            if method == "GET" and path == "/api/health":
                await self._send_json(writer, HTTPStatus.OK, {"success": True})
                return
            if method == "GET" and path == "/image":
                await self._serve_image(writer, params)
                return
            if method == "POST" and path == "/api/upload":
                await self._handle_upload(reader, writer, headers, params)
                return
            if method == "POST" and path == "/api/delete_image":
                await self._send_json(writer, HTTPStatus.OK, self._plugin._delete_outfit_image(
                    self._first(params, "outfit_id"), int(self._first(params, "index", "0") or 0)
                ))
                return
            if method == "POST" and path == "/api/select":
                await self._handle_select(writer, params)
                return
            if method == "POST" and path == "/api/reroll":
                selection = self._plugin._reroll_today()
                await self._send_json(
                    writer,
                    HTTPStatus.OK if selection is not None else HTTPStatus.BAD_REQUEST,
                    {"success": selection is not None, "outfit_id": selection.outfit_id if selection else ""},
                )
                return
            if method == "POST" and path == "/api/event/save":
                outfits = [item for item in self._first(params, "outfits").split(",") if item]
                result = self._plugin._save_event(
                    self._first(params, "id"),
                    self._first(params, "name"),
                    self._first(params, "date"),
                    outfits,
                    self._first(params, "note"),
                )
                await self._send_json(writer, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST, result)
                return
            if method == "POST" and path == "/api/event/delete":
                result = self._plugin._delete_event(self._first(params, "id"))
                await self._send_json(writer, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST, result)
                return

            await self._send_json(writer, HTTPStatus.NOT_FOUND, {"success": False, "error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._plugin.ctx.logger.debug("衣橱网页请求失败: %s", exc)
            try:
                await self._send_json(writer, HTTPStatus.INTERNAL_SERVER_ERROR, {"success": False, "error": str(exc)})
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _read_request(self, reader: asyncio.StreamReader) -> tuple[str | None, str, dict[str, str]]:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
        except asyncio.TimeoutError:
            return None, "", {}
        if not request_line:
            return None, "", {}
        try:
            method, target, _version = request_line.decode("latin-1").strip().split(" ", 2)
        except ValueError:
            return None, "", {}

        headers: dict[str, str] = {}
        total = len(request_line)
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            total += len(line)
            if total > _MAX_HEADER_BYTES:
                return None, "", {}
            if line in (b"\r\n", b"\n", b""):
                break
            key, _, value = line.decode("latin-1").partition(":")
            if key:
                headers[key.strip().lower()] = value.strip()
        return method.upper(), target, headers

    def _check_token(self, params: Mapping[str, list[str]], headers: Mapping[str, str]) -> bool:
        expected = self.token
        if not expected:
            return False
        supplied = str((params.get("token") or [""])[0]) or str(headers.get("x-wardrobe-token", "") or "")
        return bool(supplied) and secrets.compare_digest(supplied, expected)

    async def _handle_upload(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        headers: Mapping[str, str],
        params: Mapping[str, list[str]],
    ) -> None:
        if not self._plugin.config.plugin.enabled:
            await self._send_json(writer, HTTPStatus.FORBIDDEN, {"success": False, "error": "插件未启用"})
            return
        try:
            content_length = int(headers.get("content-length", "0") or 0)
        except ValueError:
            content_length = 0
        max_bytes = int(float(self._plugin.config.upload.max_file_mb) * 1024 * 1024)
        if content_length <= 0:
            await self._send_json(writer, HTTPStatus.BAD_REQUEST, {"success": False, "error": "没有收到图片数据"})
            return
        if content_length > _MAX_UPLOAD_HARD_BYTES:
            await self._send_json(writer, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"success": False, "error": "图片太大"})
            return
        try:
            body = await asyncio.wait_for(reader.readexactly(content_length), timeout=120)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            await self._send_json(writer, HTTPStatus.BAD_REQUEST, {"success": False, "error": "图片读取超时"})
            return
        if len(body) > max_bytes:
            await self._send_json(
                writer,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {
                    "success": False,
                    "error": f"图片有 {len(body) / 1024 / 1024:.1f} MB，超过 {self._plugin.config.upload.max_file_mb:g} MB 限制",
                },
            )
            return
        if detect_image_format(body) is None:
            await self._send_json(writer, HTTPStatus.BAD_REQUEST, {"success": False, "error": "只支持 PNG、JPEG、GIF、WEBP"})
            return
        result = await self._plugin._save_outfit_images(
            self._first(params, "outfit_id"),
            self._first(params, "name"),
            self._first(params, "description"),
            [body],
        )
        await self._send_json(writer, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST, result)

    async def _handle_select(self, writer: asyncio.StreamWriter, params: Mapping[str, list[str]]) -> None:
        selection = self._plugin._wear_today(self._first(params, "outfit_id"))
        if selection is None:
            await self._send_json(writer, HTTPStatus.NOT_FOUND, {"success": False, "error": "找不到这套衣服"})
            return
        await self._send_json(writer, HTTPStatus.OK, {"success": True, "outfit_id": selection.outfit_id})

    async def _serve_image(self, writer: asyncio.StreamWriter, params: Mapping[str, list[str]]) -> None:
        outfit_id = self._first(params, "outfit_id")
        try:
            index = max(0, int(self._first(params, "index", "0") or 0))
        except ValueError:
            index = 0
        wardrobe = self._plugin._wardrobe()
        outfit = wardrobe.get_outfit(outfit_id)
        if outfit is None or index >= len(outfit.images):
            await self._send_json(writer, HTTPStatus.NOT_FOUND, {"success": False, "error": "图片不存在"})
            return
        loaded = load_image_base64(self._plugin._data_dir or self._plugin._resolve_data_dir(), outfit.images[index])
        if loaded is None:
            await self._send_json(writer, HTTPStatus.NOT_FOUND, {"success": False, "error": "图片读取失败"})
            return
        image_format, image_base64 = loaded
        try:
            data = base64.b64decode(image_base64)
        except Exception:  # noqa: BLE001
            await self._send_json(writer, HTTPStatus.NOT_FOUND, {"success": False, "error": "图片解码失败"})
            return
        await self._send(writer, HTTPStatus.OK, data, mime_type_for_format(image_format), cache=True)

    # ------------------------------------------------------------------ 数据

    def _state_payload(self) -> dict[str, Any]:
        plugin = self._plugin
        wardrobe = plugin._wardrobe()
        today = plugin._today()
        selection = plugin._ensure_today(today)
        outfit = plugin._outfit_of(selection)
        events = plugin._active_events(today)
        today_payload = None
        if selection is not None:
            today_payload = {
                "outfit_id": selection.outfit_id,
                "outfit_name": outfit.display_name if outfit else selection.outfit_id,
                "description": outfit.prompt_description() if outfit else "",
                "note": selection.note,
                "source": selection.source,
                "image_count": len(outfit.images) if outfit else 0,
                "event_names": [event.name for event in events],
            }
        outfits = [
            {
                "id": item.id,
                "name": item.display_name,
                "description": item.prompt_description(),
                "image_count": len(item.images),
            }
            for item in wardrobe.outfits
        ]
        event_rows = [
            {
                "id": event.id,
                "name": event.name,
                "date": event.date,
                "outfits": list(event.outfits),
                "note": event.note,
            }
            for event in wardrobe.events
        ]
        return {
            "success": True,
            "name": plugin._display_name or "麦麦",
            "date": today.isoformat(),
            "today": today_payload,
            "outfits": outfits,
            "events": event_rows,
            "max_file_mb": float(plugin.config.upload.max_file_mb),
        }

    def _page_html(self) -> str:
        plugin = self._plugin
        title = html.escape(str(plugin.config.web_ui.page_title or "她的衣橱"))
        name = html.escape(str(plugin._display_name or "麦麦"))
        return (
            _PAGE_TEMPLATE.replace("__TITLE__", title)
            .replace("__NAME__", name)
            .replace("__TOKEN__", json.dumps(self.token))
        )

    @staticmethod
    def _first(params: Mapping[str, list[str]], key: str, default: str = "") -> str:
        values = params.get(key)
        if not values:
            return default
        return str(values[0] if values[0] is not None else default)

    async def _send_json(self, writer: asyncio.StreamWriter, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        await self._send(writer, status, body, "application/json; charset=utf-8")

    async def _send(
        self,
        writer: asyncio.StreamWriter,
        status: HTTPStatus,
        body: bytes,
        content_type: str = "text/plain; charset=utf-8",
        *,
        cache: bool = False,
    ) -> None:
        lines = [
            f"HTTP/1.1 {int(status)} {status.phrase}",
            f"Content-Type: {content_type}",
            f"Content-Length: {len(body)}",
            "Connection: close",
            "X-Content-Type-Options: nosniff",
            f"Cache-Control: {'public, max-age=60' if cache else 'no-store'}",
        ]
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
        await writer.drain()


_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f6f1e8;--card:#fffdf8;--ink:#2b2118;--muted:#7c6f60;--line:#e8ddcd;--accent:#9a3412;--accent2:#c2410c;--ok:#166534;--bad:#991b1b}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 10% -10%,#fde8d7 0,transparent 60%),var(--bg);color:var(--ink);font:15px/1.7 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}
header{padding:34px 20px 10px;text-align:center}
.brand{font-size:26px;font-weight:800;letter-spacing:1px;color:var(--accent)}
.sub{color:var(--muted);margin-top:4px}
main{max-width:980px;margin:0 auto;padding:16px 18px 70px;display:grid;gap:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px;box-shadow:0 10px 30px rgba(120,80,40,.06)}
h2{margin:0 0 14px;font-size:18px;display:flex;justify-content:space-between;align-items:center;gap:10px}
.today-head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
.big{font-size:20px;font-weight:700}
.meta{color:var(--muted);font-size:13px}
.tag{display:inline-block;background:#fde8d7;color:var(--accent);border-radius:999px;padding:2px 10px;font-size:12px;margin-right:6px}
.gallery{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
.thumb{position:relative;width:132px;height:176px}
.thumb img{width:100%;height:100%;object-fit:cover;border-radius:12px;border:1px solid var(--line);background:#f3ece2}
.thumb .del{position:absolute;top:6px;right:6px;width:26px;height:26px;border-radius:50%;border:0;background:rgba(153,27,27,.88);color:white;font-size:16px;line-height:26px;padding:0;cursor:pointer}
.drop{border:2px dashed #d9c7ae;border-radius:16px;padding:26px 16px;text-align:center;color:var(--muted);background:#fffaf1;transition:.15s}
.drop.over{border-color:var(--accent2);background:#fff1e2}
.filelabel{color:var(--accent);font-weight:700;cursor:pointer;text-decoration:underline}
.filelabel input{display:none}
.row{display:flex;gap:12px;align-items:flex-start;margin-top:14px;flex-wrap:wrap}
.row label{min-width:64px;color:var(--muted);padding-top:9px}
select,input,textarea{font:inherit;color:var(--ink);background:#fff;border:1px solid var(--line);border-radius:10px;padding:9px 12px;outline:none;min-width:220px;flex:1}
textarea{min-height:70px;resize:vertical}
button{font:inherit;font-weight:700;border:0;border-radius:12px;padding:10px 18px;background:linear-gradient(135deg,var(--accent2),var(--accent));color:#fff;cursor:pointer}
button:disabled{opacity:.5;cursor:not-allowed}
button.ghost{background:#fff;color:var(--accent);border:1px solid var(--line);padding:7px 12px}
button.danger{background:#fff;color:var(--bad);border:1px solid #f0c4c4;padding:7px 12px}
.status{margin-top:12px;color:var(--muted);white-space:pre-wrap}
.status.ok{color:var(--ok)} .status.bad{color:var(--bad)}
.list{display:grid;gap:12px}
.item{border-bottom:1px dashed var(--line);padding:10px 0}
.item:last-child{border-bottom:0}
.item-top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
.mini-gallery{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.mini{position:relative;width:54px;height:68px}
.mini img{width:100%;height:100%;object-fit:cover;border-radius:8px;border:1px solid var(--line);background:#f3ece2}
.mini .del{width:20px;height:20px;font-size:13px;line-height:20px;top:-6px;right:-6px}
.actions{display:flex;gap:6px;flex-wrap:wrap;flex:none}
.event-item{border:1px solid var(--line);border-radius:12px;padding:12px;display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
.event-item .name{font-weight:700}
.event-form{border-top:1px dashed var(--line);margin-top:16px;padding-top:14px}
.checkgrid{display:flex;flex-wrap:wrap;gap:8px 14px;margin-top:8px}
.checkgrid label{display:flex;align-items:center;gap:5px;font-size:14px;color:var(--ink);padding:0;min-width:0}
.checkgrid input{min-width:0;width:auto;flex:none}
.hidden{display:none}
.foot{text-align:center;color:var(--muted);font-size:12px;padding:10px 0 30px}
@media(max-width:640px){.row{display:block}.row label{display:block;padding:0 0 6px}select,input,textarea{width:100%}.thumb{width:46%;height:180px}.item-top{display:block}.actions{margin-top:8px}}
</style>
</head>
<body>
<header>
  <div class="brand">👗 __TITLE__</div>
  <div class="sub">__NAME__ 的穿搭 · 上传图片、每天随机、写自己的事件</div>
</header>
<main>
  <section class="card">
    <h2>今天的穿搭 <button id="reroll" class="ghost">换一套</button></h2>
    <div id="today"><span class="meta">加载中…</span></div>
    <div id="today-thumbs" class="gallery"></div>
  </section>

  <section class="card">
    <h2>上传图片</h2>
    <div id="drop" class="drop">
      把图片拖到这里，或 <label class="filelabel">选择图片<input id="file" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple></label>
      <div id="filename" class="meta" style="margin-top:6px"></div>
    </div>
    <div class="row"><label for="outfit">穿给</label><select id="outfit"></select></div>
    <div id="newbox" class="row hidden">
      <label for="newid">新 ID</label><input id="newid" placeholder="例如 spring_dress">
      <label for="newname">名字</label><input id="newname" placeholder="例如 春日白裙">
    </div>
    <div class="row"><label for="description">描述</label><textarea id="description" placeholder="可选：写给模型看的描述。留空会由视觉模型识别。"></textarea></div>
    <div class="row"><button id="upload">上传</button></div>
    <div id="status" class="status"></div>
  </section>

  <section class="card">
    <h2>衣橱</h2>
    <div id="list" class="list"></div>
  </section>

  <section class="card">
    <h2>特殊事件 <button id="new-event" class="ghost">添加事件</button></h2>
    <div id="events" class="list"></div>
    <div id="event-form" class="event-form hidden">
      <div class="row"><label for="event-name">名称</label><input id="event-name" placeholder="例如 生日 / 圣诞 / 期末周"></div>
      <div class="row"><label for="event-date">日期</label><input id="event-date" placeholder="10-28 / 2026-10-28 / 12-24..12-26"></div>
      <div class="row"><label>穿哪套</label><div id="event-outfits" class="checkgrid"></div></div>
      <div class="row"><label for="event-note">说明</label><textarea id="event-note" placeholder="可选：写给模型看的一句话，例如「今天是生日，穿了白色连衣裙」。"></textarea></div>
      <div class="row">
        <button id="event-save">保存事件</button>
        <button id="event-cancel" class="ghost">取消</button>
      </div>
      <div id="event-status" class="status"></div>
    </div>
  </section>
</main>
<div class="foot">本地页面只监听 127.0.0.1；图片不会上传到第三方</div>
<script>
const TOKEN = __TOKEN__;
const API = function(path){ return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN); };
let state = null;
let files = [];
let editingEventId = '';

function esc(value){
  return String(value == null ? '' : value).replace(/[&<>"']/g, function(ch){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];
  });
}
function setStatus(text, kind){
  const node = document.getElementById('status');
  node.className = 'status' + (kind ? ' ' + kind : '');
  node.textContent = text || '';
}
function setEventStatus(text, kind){
  const node = document.getElementById('event-status');
  node.className = 'status' + (kind ? ' ' + kind : '');
  node.textContent = text || '';
}
async function apiFetch(path, options){
  let response;
  try {
    response = await fetch(API(path), options || {});
  } catch (err) {
    throw new Error('连接已断开：插件可能刚重载或页面已过期。请回到 MaiBot 首页重新打开「她的衣橱」卡片。');
  }
  let payload = null;
  try { payload = await response.json(); } catch (err) { payload = null; }
  if (!response.ok) throw new Error(payload && payload.error ? payload.error : ('HTTP ' + response.status));
  return payload || {};
}
function imageUrl(outfitId, index){
  return API('/image?outfit_id=' + encodeURIComponent(outfitId) + '&index=' + index);
}
async function loadState(){
  state = await apiFetch('/api/state');
  renderToday();
  renderOutfits();
  renderEvents();
  renderSelect();
}
function renderToday(){
  const node = document.getElementById('today');
  const thumbs = document.getElementById('today-thumbs');
  thumbs.innerHTML = '';
  if (!state.today) {
    node.innerHTML = '<div class="meta">今天还没有衣服，先在下面上传一套吧。</div>';
    return;
  }
  const today = state.today;
  const tags = (today.event_names || []).map(function(name){ return '<span class="tag">' + esc(name) + '</span>'; }).join('');
  node.innerHTML = '<div class="today-head"><div><div class="big">' + esc(today.outfit_name) + '</div>'
    + '<div class="meta">' + esc(state.date) + ' · ' + esc(today.outfit_id) + '</div>'
    + '<div style="margin-top:6px">' + tags + '</div>'
    + '<div class="meta" style="margin-top:6px">' + esc(today.description || '暂无描述') + '</div></div></div>';
  for (let i = 0; i < (today.image_count || 0); i++) {
    thumbs.appendChild(makeThumb(today.outfit_id, i, false));
  }
}
function makeThumb(outfitId, index, mini){
  const wrap = document.createElement('div');
  wrap.className = mini ? 'mini' : 'thumb';
  const img = document.createElement('img');
  img.src = imageUrl(outfitId, index);
  img.alt = outfitId;
  wrap.appendChild(img);
  const del = document.createElement('button');
  del.className = 'del';
  del.textContent = '×';
  del.title = '删除这张图片';
  del.addEventListener('click', function(){ deleteImage(outfitId, index); });
  wrap.appendChild(del);
  return wrap;
}
async function deleteImage(outfitId, index){
  if (!confirm('确定删除这张图片吗？删除后不能恢复。')) return;
  try {
    await apiFetch('/api/delete_image?outfit_id=' + encodeURIComponent(outfitId) + '&index=' + index, {method:'POST'});
    await loadState();
    setStatus('已删除图片。', 'ok');
  } catch (error) {
    setStatus('删除失败：' + error.message, 'bad');
  }
}
function renderOutfits(){
  const list = document.getElementById('list');
  list.innerHTML = '';
  if (!state.outfits.length) {
    list.innerHTML = '<div class="meta">衣橱还是空的。</div>';
    return;
  }
  state.outfits.forEach(function(item){
    const row = document.createElement('div');
    row.className = 'item';
    const top = document.createElement('div');
    top.className = 'item-top';
    top.innerHTML = '<div><div><strong>' + esc(item.name) + '</strong>'
      + '<span class="tag">' + esc(item.id) + '</span></div>'
      + '<div class="meta">' + item.image_count + ' 张图</div></div>';
    const actions = document.createElement('div');
    actions.className = 'actions';
    const wear = document.createElement('button');
    wear.className = 'ghost';
    wear.textContent = '穿这套';
    wear.addEventListener('click', async function(){
      wear.disabled = true;
      try {
        await apiFetch('/api/select?outfit_id=' + encodeURIComponent(item.id), {method:'POST'});
        await loadState();
        setStatus('今天穿「' + item.name + '」啦。', 'ok');
      } catch (error) { setStatus('设置失败：' + error.message, 'bad'); }
      finally { wear.disabled = false; }
    });
    const add = document.createElement('button');
    add.className = 'ghost';
    add.textContent = '补图';
    add.addEventListener('click', function(){
      document.getElementById('outfit').value = item.id;
      document.getElementById('outfit').dispatchEvent(new Event('change'));
      document.getElementById('drop').scrollIntoView({behavior:'smooth', block:'center'});
    });
    actions.appendChild(wear);
    actions.appendChild(add);
    top.appendChild(actions);
    row.appendChild(top);
    if (item.image_count > 0) {
      const gallery = document.createElement('div');
      gallery.className = 'mini-gallery';
      for (let i = 0; i < item.image_count; i++) gallery.appendChild(makeThumb(item.id, i, true));
      row.appendChild(gallery);
    }
    list.appendChild(row);
  });
}
function renderEvents(){
  const list = document.getElementById('events');
  list.innerHTML = '';
  if (!state.events.length) {
    list.innerHTML = '<div class="meta">还没有事件。点「添加事件」写一个，例如生日 10-28 穿白色连衣裙。</div>';
    return;
  }
  state.events.forEach(function(event){
    const row = document.createElement('div');
    row.className = 'event-item';
    row.innerHTML = '<div><div class="name">' + esc(event.name) + '</div>'
      + '<div class="meta">' + esc(event.date) + ' · ' + esc((event.outfits || []).join('、')) + '</div>'
      + (event.note ? '<div class="meta">' + esc(event.note) + '</div>' : '') + '</div>';
    const actions = document.createElement('div');
    actions.className = 'actions';
    const edit = document.createElement('button');
    edit.className = 'ghost';
    edit.textContent = '编辑';
    edit.addEventListener('click', function(){ startEditEvent(event); });
    const del = document.createElement('button');
    del.className = 'danger';
    del.textContent = '删除';
    del.addEventListener('click', function(){ deleteEvent(event); });
    actions.appendChild(edit);
    actions.appendChild(del);
    row.appendChild(actions);
    list.appendChild(row);
  });
}
function renderSelect(){
  const select = document.getElementById('outfit');
  const current = state.today ? state.today.outfit_id : '';
  select.innerHTML = '';
  state.outfits.forEach(function(item){
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = item.name + ' (' + item.id + ') · ' + item.image_count + ' 张';
    if (item.id === current) option.selected = true;
    select.appendChild(option);
  });
  const option = document.createElement('option');
  option.value = '__new__';
  option.textContent = '＋ 新建一套衣服';
  select.appendChild(option);
  toggleNew();
  renderEventOutfitChecks();
}
function toggleNew(){
  document.getElementById('newbox').classList.toggle('hidden', document.getElementById('outfit').value !== '__new__');
}
function renderEventOutfitChecks(selected){
  const box = document.getElementById('event-outfits');
  const checked = new Set(selected || []);
  box.innerHTML = '';
  if (!state.outfits.length) {
    box.innerHTML = '<span class="meta">先上传一套衣服。</span>';
    return;
  }
  state.outfits.forEach(function(item){
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.className = 'event-outfit';
    input.value = item.id;
    if (checked.has(item.id)) input.checked = true;
    label.appendChild(input);
    label.appendChild(document.createTextNode(item.name + ' (' + item.id + ')'));
    box.appendChild(label);
  });
}
function resetEventForm(){
  editingEventId = '';
  document.getElementById('event-name').value = '';
  document.getElementById('event-date').value = '';
  document.getElementById('event-note').value = '';
  document.getElementById('event-form').classList.add('hidden');
  setEventStatus('');
  renderEventOutfitChecks([]);
}
function startEditEvent(event){
  editingEventId = event.id;
  document.getElementById('event-form').classList.remove('hidden');
  document.getElementById('event-name').value = event.name || '';
  document.getElementById('event-date').value = event.date || '';
  document.getElementById('event-note').value = event.note || '';
  renderEventOutfitChecks(event.outfits || []);
  document.getElementById('event-form').scrollIntoView({behavior:'smooth', block:'center'});
}
async function deleteEvent(event){
  if (!confirm('确定删除事件「' + event.name + '」吗？')) return;
  try {
    await apiFetch('/api/event/delete?id=' + encodeURIComponent(event.id), {method:'POST'});
    await loadState();
    setStatus('已删除事件。', 'ok');
  } catch (error) { setStatus('删除失败：' + error.message, 'bad'); }
}
function updateFiles(list){
  files = Array.prototype.slice.call(list || []);
  document.getElementById('filename').textContent = files.length ? files.map(function(file){ return file.name; }).join('、') : '';
}
document.getElementById('file').addEventListener('change', function(event){ updateFiles(event.target.files); });
const drop = document.getElementById('drop');
['dragenter','dragover'].forEach(function(name){
  drop.addEventListener(name, function(event){ event.preventDefault(); drop.classList.add('over'); });
});
['dragleave','drop'].forEach(function(name){
  drop.addEventListener(name, function(event){ event.preventDefault(); drop.classList.remove('over'); });
});
drop.addEventListener('drop', function(event){ updateFiles(event.dataTransfer.files); });
document.getElementById('outfit').addEventListener('change', toggleNew);

document.getElementById('upload').addEventListener('click', async function(){
  const button = document.getElementById('upload');
  const select = document.getElementById('outfit');
  const isNew = select.value === '__new__';
  const outfitId = isNew ? document.getElementById('newid').value.trim() : select.value;
  const name = isNew ? document.getElementById('newname').value.trim() : '';
  const description = document.getElementById('description').value.trim();
  if (!outfitId) { setStatus('请先填写新衣服的 ID。', 'bad'); return; }
  if (!files.length) { setStatus('请先选择图片。', 'bad'); return; }
  const maxMb = Number(state && state.max_file_mb) || 16;
  const tooBig = files.find(function(file){ return file.size > maxMb * 1024 * 1024; });
  if (tooBig) {
    setStatus('「' + tooBig.name + '」有 ' + (tooBig.size / 1024 / 1024).toFixed(1)
      + ' MB，超过 ' + maxMb + ' MB 上限。', 'bad');
    return;
  }
  button.disabled = true;
  try {
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      setStatus('正在上传 ' + (i + 1) + '/' + files.length + '：' + file.name + ' …');
      const query = '/api/upload?outfit_id=' + encodeURIComponent(outfitId)
        + '&name=' + encodeURIComponent(name)
        + '&description=' + encodeURIComponent(description);
      const payload = await apiFetch(query, {
        method: 'POST',
        headers: {'Content-Type': file.type || 'application/octet-stream'},
        body: file,
      });
      setStatus(payload.message || '上传成功', 'ok');
    }
    updateFiles([]);
    document.getElementById('file').value = '';
    document.getElementById('description').value = '';
    await loadState();
    setStatus('完成。想让她今天穿这套，点衣橱里的「穿这套」。', 'ok');
  } catch (error) {
    setStatus('上传失败：' + error.message, 'bad');
  } finally {
    button.disabled = false;
  }
});

document.getElementById('reroll').addEventListener('click', async function(){
  const button = document.getElementById('reroll');
  button.disabled = true;
  try {
    await apiFetch('/api/reroll', {method:'POST'});
    await loadState();
    setStatus('已经换了一套。', 'ok');
  } catch (error) { setStatus('换装失败：' + error.message, 'bad'); }
  finally { button.disabled = false; }
});

document.getElementById('new-event').addEventListener('click', function(){
  resetEventForm();
  document.getElementById('event-form').classList.remove('hidden');
  document.getElementById('event-name').focus();
});
document.getElementById('event-cancel').addEventListener('click', resetEventForm);
document.getElementById('event-save').addEventListener('click', async function(){
  const name = document.getElementById('event-name').value.trim();
  const dateText = document.getElementById('event-date').value.trim();
  const note = document.getElementById('event-note').value.trim();
  const outfits = Array.prototype.slice.call(document.querySelectorAll('.event-outfit:checked')).map(function(node){ return node.value; });
  if (!name) { setEventStatus('事件名称不能为空。', 'bad'); return; }
  if (!dateText) { setEventStatus('事件日期不能为空。', 'bad'); return; }
  if (!outfits.length) { setEventStatus('至少勾选一套衣服。', 'bad'); return; }
  const query = '/api/event/save?id=' + encodeURIComponent(editingEventId)
    + '&name=' + encodeURIComponent(name)
    + '&date=' + encodeURIComponent(dateText)
    + '&outfits=' + encodeURIComponent(outfits.join(','))
    + '&note=' + encodeURIComponent(note);
  try {
    await apiFetch(query, {method:'POST'});
    editingEventId = '';
    document.getElementById('event-name').value = '';
    document.getElementById('event-date').value = '';
    document.getElementById('event-note').value = '';
    renderEventOutfitChecks([]);
    await loadState();
    setEventStatus('事件已保存。', 'ok');
  } catch (error) { setEventStatus('保存失败：' + error.message, 'bad'); }
});

loadState().catch(function(error){
  setStatus('加载失败：' + error.message, 'bad');
});
</script>
</body>
</html>
"""
