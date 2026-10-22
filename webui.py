"""插件自带的本地网页上传服务。

在 MaiBot 首页注册一张卡片，点开后是一个只监听 ``127.0.0.1`` 的上传页；
页面用一次性令牌鉴权，不经过 QQ，也不需要额外的反向代理。
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
    """插件内置的 HTTP 上传页。"""

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin
        self._server: asyncio.AbstractServer | None = None
        self._socket: Any = None

    @property
    def token(self) -> str:
        return str(getattr(self._plugin, "_web_token", "") or "")

    async def start(self, sock: Any) -> bool:
        """在已经 bind/listen 的 socket 上启动服务。"""

        if self._server is not None:
            return True
        try:
            self._socket = sock
            self._server = await asyncio.start_server(self._handle_connection, sock=sock)
            return True
        except Exception as exc:  # noqa: BLE001
            self._server = None
            self._plugin.ctx.logger.warning("衣橱网页上传服务启动失败: %s", exc)
            return False

    async def stop(self) -> None:
        """关闭服务并释放监听端口。"""

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

    # ------------------------------------------------------------------ 请求处理

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, target, headers = await self._read_request(reader)
            if method is None:
                await self._send(writer, HTTPStatus.BAD_REQUEST, b"bad request")
                return

            path, _, query = target.partition("?")
            params = parse_qs(query, keep_blank_values=True, max_num_fields=50)
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
                await self._send_json(writer, HTTPStatus.OK, {"success": True, "plugin": "wardrobe"})
                return
            if method == "GET" and path == "/image":
                await self._serve_image(writer, params)
                return
            if method == "POST" and path == "/api/upload":
                await self._handle_upload(reader, writer, headers, params)
                return
            if method == "POST" and path == "/api/describe":
                await self._handle_describe(writer, params)
                return

            await self._send_json(writer, HTTPStatus.NOT_FOUND, {"success": False, "error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._plugin.ctx.logger.debug("衣橱网页请求处理失败: %s", exc)
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
        supplied = ""
        if params.get("token"):
            supplied = str(params["token"][0])
        if not supplied:
            supplied = str(headers.get("x-wardrobe-token", "") or "")
        return bool(supplied) and secrets.compare_digest(supplied, expected)


    async def _handle_upload(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        headers: Mapping[str, str],
        params: Mapping[str, list[str]],
    ) -> None:
        if not self._plugin.config.upload.enabled:
            await self._send_json(writer, HTTPStatus.FORBIDDEN, {"success": False, "error": "图片上传已关闭"})
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
            await self._send_json(
                writer,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"success": False, "error": "图片太大，单张不能超过 64 MB"},
            )
            return
        # 先完整读取请求体再判断大小，避免服务端提前关闭连接导致浏览器只看到
        # “Failed to fetch”，而不是可读的 413 提示。
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
            await self._send_json(
                writer,
                HTTPStatus.BAD_REQUEST,
                {"success": False, "error": "只支持 PNG、JPEG、GIF、WEBP 图片"},
            )
            return

        outfit_id = self._first(params, "outfit_id")
        name = self._first(params, "name")
        caption = self._first(params, "caption")
        if not outfit_id:
            await self._send_json(writer, HTTPStatus.BAD_REQUEST, {"success": False, "error": "缺少 outfit_id"})
            return

        result = await self._plugin._save_outfit_images(outfit_id, name, caption, [body])  # noqa: SLF001
        status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
        await self._send_json(writer, status, result)

    async def _handle_describe(self, writer: asyncio.StreamWriter, params: Mapping[str, list[str]]) -> None:
        if not self._plugin.config.vision.enabled:
            await self._send_json(writer, HTTPStatus.FORBIDDEN, {"success": False, "error": "视觉描述已关闭"})
            return
        outfit_id = self._first(params, "outfit_id")
        wardrobe = self._plugin._wardrobe()
        outfit = wardrobe.get_outfit(outfit_id) if outfit_id else None
        if outfit is None:
            await self._send_json(writer, HTTPStatus.NOT_FOUND, {"success": False, "error": "找不到这套衣服"})
            return
        if not outfit.images:
            await self._send_json(writer, HTTPStatus.BAD_REQUEST, {"success": False, "error": "这套衣服还没有图片"})
            return
        try:
            description = await self._plugin._describe_outfit_image(outfit, outfit.images[-1])
        except Exception as exc:  # noqa: BLE001
            await self._send_json(writer, HTTPStatus.BAD_REQUEST, {"success": False, "error": str(exc)})
            return
        if not description:
            await self._send_json(writer, HTTPStatus.BAD_REQUEST, {"success": False, "error": "视觉模型没有返回描述"})
            return
        outfit.vision_description = description
        if self._plugin._store is not None:
            self._plugin._store.save(wardrobe)
        await self._send_json(writer, HTTPStatus.OK, {"success": True, "description": description})

    async def _serve_image(self, writer: asyncio.StreamWriter, params: Mapping[str, list[str]]) -> None:
        outfit_id = self._first(params, "outfit_id")
        index_text = self._first(params, "index", "0")
        try:
            index = max(0, int(index_text))
        except ValueError:
            index = 0
        wardrobe = self._plugin._wardrobe()
        outfit = wardrobe.get_outfit(outfit_id) if outfit_id else None
        if outfit is None or index >= len(outfit.images):
            await self._send_json(writer, HTTPStatus.NOT_FOUND, {"success": False, "error": "图片不存在"})
            return
        loaded = load_image_base64(self._plugin._data_dir or self._plugin._resolve_data_dir(), outfit.images[index])
        if loaded is None:
            await self._send_json(writer, HTTPStatus.NOT_FOUND, {"success": False, "error": "图片文件读取失败"})
            return
        image_format, image_base64 = loaded
        try:
            data = base64.b64decode(image_base64)
        except Exception:  # noqa: BLE001
            await self._send_json(writer, HTTPStatus.NOT_FOUND, {"success": False, "error": "图片解码失败"})
            return
        await self._send(writer, HTTPStatus.OK, data, mime_type_for_format(image_format), cache=True)

    # ------------------------------------------------------------------ 数据与页面

    def _state_payload(self) -> dict[str, Any]:
        plugin = self._plugin
        wardrobe = plugin._wardrobe()
        today = plugin._today()
        selection = plugin._ensure_today(today)
        outfit = plugin._outfit_of(selection)
        events = plugin._active_events(today, selection)
        today_payload = None
        if selection is not None:
            today_payload = {
                "outfit_id": selection.outfit_id,
                "outfit_name": outfit.display_name if outfit else selection.outfit_id,
                "description": outfit.prompt_description() if outfit else "",
                "season": selection.season,
                "source": selection.source,
                "note": selection.note,
                "image_count": len(outfit.images) if outfit else 0,
                "event_names": [event.name for event in events],
            }
        outfits = [
            {
                "id": item.id,
                "name": item.display_name,
                "season": item.season_text(),
                "image_count": len(item.images),
                "only_for_events": item.only_for_events,
                "description": item.prompt_description(),
                "enabled": item.enabled,
            }
            for item in wardrobe.outfits
        ]
        return {
            "success": True,
            "name": plugin._display_name or "麦麦",
            "date": today.isoformat(),
            "season": plugin._season_of(today),
            "today": today_payload,
            "outfits": outfits,
            "upload_enabled": bool(plugin.config.upload.enabled),
            "max_file_mb": float(plugin.config.upload.max_file_mb),
            "vision_enabled": bool(plugin.config.vision.enabled),
        }

    def _page_html(self) -> str:
        plugin = self._plugin
        title = html.escape(str(plugin.config.web_ui.page_title or "她的衣橱"))
        name = html.escape(str(plugin._display_name or "麦麦"))
        token_json = json.dumps(self.token)
        return (
            _PAGE_TEMPLATE.replace("__TITLE__", title)
            .replace("__NAME__", name)
            .replace("__TOKEN__", token_json)
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
        reason = status.phrase
        lines = [
            f"HTTP/1.1 {int(status)} {reason}",
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
h2{margin:0 0 14px;font-size:18px}
.today{display:flex;gap:18px;align-items:center;flex-wrap:wrap}
.today .big{font-size:20px;font-weight:700}
.tag{display:inline-block;background:#fde8d7;color:var(--accent);border-radius:999px;padding:2px 10px;font-size:12px;margin-right:6px}
.thumbs{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}
.thumbs img{width:132px;height:176px;object-fit:cover;border-radius:12px;border:1px solid var(--line);background:#f3ece2}
.drop{border:2px dashed #d9c7ae;border-radius:16px;padding:26px 16px;text-align:center;color:var(--muted);background:#fffaf1;transition:.15s}
.drop.over{border-color:var(--accent2);background:#fff1e2}
.filelabel{color:var(--accent);font-weight:700;cursor:pointer;text-decoration:underline}
.filelabel input{display:none}
.row{display:flex;gap:12px;align-items:flex-start;margin-top:14px;flex-wrap:wrap}
.row label{min-width:64px;color:var(--muted);padding-top:9px}
select,input,textarea{font:inherit;color:var(--ink);background:#fff;border:1px solid var(--line);border-radius:10px;padding:9px 12px;outline:none;min-width:220px;flex:1}
textarea{min-height:74px;resize:vertical}
button{font:inherit;font-weight:700;border:0;border-radius:12px;padding:12px 22px;background:linear-gradient(135deg,var(--accent2),var(--accent));color:#fff;cursor:pointer}
button:disabled{opacity:.5;cursor:not-allowed}
button.ghost{background:#fff;color:var(--accent);border:1px solid var(--line);padding:8px 14px}
.status{margin-top:12px;color:var(--muted);white-space:pre-wrap}
.status.ok{color:var(--ok)}
.status.bad{color:var(--bad)}
.list{display:grid;gap:10px}
.item{display:flex;justify-content:space-between;gap:12px;align-items:center;border-bottom:1px dashed var(--line);padding:8px 0}
.item:last-child{border-bottom:0}
.item .meta{color:var(--muted);font-size:13px}
.mini{width:44px;height:56px;object-fit:cover;border-radius:8px;border:1px solid var(--line);background:#f3ece2;flex:none}
.badge{background:#f3ece2;color:var(--muted);border-radius:999px;padding:1px 8px;font-size:12px;margin-left:6px}
.hidden{display:none}
.foot{text-align:center;color:var(--muted);font-size:12px;padding:10px 0 30px}
@media(max-width:640px){.row{display:block}.row label{display:block;padding:0 0 6px}select,input,textarea{width:100%}.thumbs img{width:46%;height:180px}}
</style>
</head>
<body>
<header>
  <div class="brand">👗 __TITLE__</div>
  <div class="sub">__NAME__ 的形象与穿搭 · 不上 QQ 也能换装</div>
</header>
<main>
  <section class="card">
    <h2>今天的穿搭</h2>
    <div id="today" class="today"><span class="muted">加载中…</span></div>
    <div id="thumbs" class="thumbs"></div>
  </section>

  <section class="card">
    <h2>上传图片</h2>
    <div id="drop" class="drop">
      把图片拖到这里，或 <label class="filelabel">选择图片<input id="file" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple></label>
      <div id="filename" class="meta" style="margin-top:6px"></div>
    </div>
    <div class="row">
      <label for="outfit">穿给</label>
      <select id="outfit"></select>
    </div>
    <div id="newbox" class="row hidden">
      <label for="newid">新 ID</label><input id="newid" placeholder="例如 spring_dress">
      <label for="newname">名字</label><input id="newname" placeholder="例如 春日白裙">
    </div>
    <div class="row">
      <label for="caption">描述</label>
      <textarea id="caption" placeholder="可选：写给模型看的描述，例如「米白色针织开衫配浅蓝百褶裙」。留空会由视觉模型生成。"></textarea>
    </div>
    <div class="row">
      <button id="upload">上传</button>
      <button id="describe" class="ghost">重新生成今天这套的视觉描述</button>
    </div>
    <div id="status" class="status"></div>
  </section>

  <section class="card">
    <h2>衣橱</h2>
    <div id="list" class="list"></div>
  </section>
</main>
<div class="foot">她的衣橱 · 本地页面只监听 127.0.0.1，图片不会上传到第三方</div>
<script>
const TOKEN = __TOKEN__;
const API = function(path){ return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN); };
let state = null;
let files = [];

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
async function apiFetch(path, options){
  let response;
  try {
    response = await fetch(API(path), options || {});
  } catch (err) {
    throw new Error('连接已断开：插件可能刚刚重载或页面已过期。请回到 MaiBot 首页重新打开「她的衣橱」卡片。');
  }
  let payload = null;
  try { payload = await response.json(); } catch (err) { payload = null; }
  if (!response.ok) {
    const message = payload && payload.error ? payload.error : ('HTTP ' + response.status);
    throw new Error(message);
  }
  return payload || {};
}
async function loadState(){
  state = await apiFetch('/api/state');
  renderToday();
  renderOutfits();
  renderSelect();
}
function renderToday(){
  const node = document.getElementById('today');
  const thumbs = document.getElementById('thumbs');
  thumbs.innerHTML = '';
  if (!state.today) {
    node.innerHTML = '<span class="muted">今天还没有衣服，先在下面新建一套吧。</span>';
    return;
  }
  const today = state.today;
  const events = (today.event_names || []).map(function(name){ return '<span class="tag">' + esc(name) + '</span>'; }).join('');
  node.innerHTML = '<div><div class="big">' + esc(today.outfit_name) + '</div>'
    + '<div class="meta">' + esc(state.date) + ' · ' + esc(seasonName(state.season)) + ' · ' + esc(today.outfit_id) + '</div>'
    + '<div style="margin-top:6px">' + events + '</div>'
    + '<div class="meta" style="margin-top:6px">' + esc(today.description || '暂无描述') + '</div></div>';
  for (let i = 0; i < (today.image_count || 0); i++) {
    const img = document.createElement('img');
    img.src = API('/image?outfit_id=' + encodeURIComponent(today.outfit_id) + '&index=' + i);
    img.alt = today.outfit_name;
    thumbs.appendChild(img);
  }
}
function renderOutfits(){
  const list = document.getElementById('list');
  list.innerHTML = '';
  const items = state.outfits || [];
  if (!items.length) {
    list.innerHTML = '<div class="muted">衣橱还是空的。</div>';
    return;
  }
  items.forEach(function(item){
    const row = document.createElement('div');
    row.className = 'item';
    const badge = item.only_for_events ? '<span class="badge">仅事件</span>' : '';
    const thumb = item.image_count
      ? '<img class="mini" src="' + API('/image?outfit_id=' + encodeURIComponent(item.id) + '&index=0') + '" alt="">'
      : '';
    row.innerHTML = '<div style="display:flex;gap:10px;align-items:center;min-width:0">'
      + thumb
      + '<div><div><strong>' + esc(item.name) + '</strong>' + badge
      + '<span class="badge">' + esc(item.id) + '</span></div>'
      + '<div class="meta">' + esc(item.season) + ' · ' + item.image_count + ' 张图</div></div></div>'
      + '<button class="ghost" data-id="' + esc(item.id) + '">补图</button>';
    row.querySelector('button').addEventListener('click', function(){
      document.getElementById('outfit').value = item.id;
      document.getElementById('outfit').dispatchEvent(new Event('change'));
      document.getElementById('drop').scrollIntoView({behavior:'smooth', block:'center'});
    });
    list.appendChild(row);
  });
}
function renderSelect(){
  const select = document.getElementById('outfit');
  const current = state.today ? state.today.outfit_id : '';
  select.innerHTML = '';
  (state.outfits || []).forEach(function(item){
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
}
function toggleNew(){
  const isNew = document.getElementById('outfit').value === '__new__';
  document.getElementById('newbox').classList.toggle('hidden', !isNew);
}
function seasonName(value){
  return {spring:'春天', summer:'夏天', autumn:'秋天', winter:'冬天'}[value] || value || '';
}
function updateFiles(list){
  files = Array.prototype.slice.call(list || []);
  document.getElementById('filename').textContent = files.length
    ? files.map(function(file){ return file.name; }).join('、')
    : '';
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
  const caption = document.getElementById('caption').value.trim();
  if (!outfitId) { setStatus('请先填写新衣服的 ID。', 'bad'); return; }
  if (!files.length) { setStatus('请先选择图片。', 'bad'); return; }
  const maxMb = Number(state && state.max_file_mb) || 16;
  const tooBig = files.find(function(file){ return file.size > maxMb * 1024 * 1024; });
  if (tooBig) {
    setStatus('「' + tooBig.name + '」有 ' + (tooBig.size / 1024 / 1024).toFixed(1)
      + ' MB，超过 ' + maxMb + ' MB 上限。请先压缩图片，或到 config.toml 的 [upload].max_file_mb 调大。', 'bad');
    return;
  }
  button.disabled = true;
  let success = 0;
  try {
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      setStatus('正在上传 ' + (i + 1) + '/' + files.length + '：' + file.name + ' …');
      const query = '/api/upload?outfit_id=' + encodeURIComponent(outfitId)
        + '&name=' + encodeURIComponent(name)
        + '&caption=' + encodeURIComponent(caption);
      const payload = await apiFetch(query, {
        method: 'POST',
        headers: {'Content-Type': file.type || 'application/octet-stream'},
        body: file,
      });
      success += 1;
      setStatus((payload.message || '上传成功') + (payload.description ? '\n视觉描述：' + payload.description : ''), 'ok');
    }
    updateFiles([]);
    document.getElementById('file').value = '';
    document.getElementById('caption').value = '';
    await loadState();
    setStatus('完成，共上传 ' + success + ' 张。', 'ok');
  } catch (error) {
    setStatus('上传失败：' + error.message, 'bad');
  } finally {
    button.disabled = false;
  }
});

document.getElementById('describe').addEventListener('click', async function(){
  if (!state || !state.today) { setStatus('今天还没有穿搭。', 'bad'); return; }
  const button = document.getElementById('describe');
  button.disabled = true;
  setStatus('正在让视觉模型描述今天的照片 …');
  try {
    const payload = await apiFetch('/api/describe?outfit_id=' + encodeURIComponent(state.today.outfit_id), {method:'POST'});
    await loadState();
    setStatus('已更新描述：' + (payload.description || ''), 'ok');
  } catch (error) {
    setStatus('生成失败：' + error.message, 'bad');
  } finally {
    button.disabled = false;
  }
});

loadState().catch(function(error){
  setStatus('加载失败：' + error.message + '\n请回到 MaiBot 首页重新打开这张卡片。', 'bad');
});
</script>
</body>
</html>
"""
