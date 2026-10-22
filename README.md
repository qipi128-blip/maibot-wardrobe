# 她的衣橱 · MaiBot Wardrobe

让麦麦知道自己穿什么的小插件。只保留三件事：

1. **上传图片**，视觉模型识别她的外貌和穿搭，模型聊天时就知道自己今天穿什么。
2. **每天随机换一套**衣服，同一天结果稳定。
3. **自己写特殊事件**：到了事件日期，就穿事件指定的衣服。

没有季节标签、农历、自拍、预览等额外功能。所有操作都在 MaiBot 首页的「她的衣橱」本地页面完成，不用在 QQ 里发指令。

> 适用：MaiBot 1.2.0+ / 插件 SDK 2.8.0+。

---

## 安装

1. 把仓库放到 `MaiBot/plugins/maibot-wardrobe/`，或在插件市场安装。
2. 重启 MaiBot，或在 WebUI 插件页重载。
3. 首次加载会生成：
   - `plugins/maibot-wardrobe/config.toml`
   - `data/plugins/github.qipi128-blip.wardrobe/wardrobe.toml`
   - `data/plugins/github.qipi128-blip.wardrobe/state.json`

然后在 MaiBot 首页找到 **「她的衣橱」** 卡片，点 **打开衣橱**。

---

## 网页里能做什么

### 上传图片

1. 拖拽图片到上传区，或选择图片。
2. 选择「穿给哪套衣服」，或「＋ 新建一套衣服」并填 ID / 名字。
3. 描述可留空，视觉模型会自动识别。
4. 点上传。

上传后会显示识别结果。每套衣服的图片可以单独点 **×** 删除，删除后不能恢复。

如果整套衣服都不想要了，点条目右侧的 **「删除这套」**：会删除这套衣服、它的图片目录，并把它从事件里移除；如果删的是今天这套，会自动重新抽一套。

### 换装

- 今天这套可以直接点 **「穿这套」**。
- 想重新随机，点今日穿搭右上角的 **「换一套」**。

### 特殊事件

- 点 **「添加事件」**，填写名称、日期、勾选要穿的衣服、写一句说明。
- 事件列表里可以 **编辑** 或 **删除**。
- 到了事件日期，当天会优先从事件指定的衣服里随机选一套；事件说明也会写进模型提示词。

支持的日期写法：

| 写法 | 含义 |
| --- | --- |
| `10-28` | 每年 10 月 28 日 |
| `2026-10-28` | 仅 2026 年 |
| `12-24..12-26` | 每年 12 月 24 日到 26 日 |
| `2026-12-24..2026-12-26` | 指定年份区间 |

分隔符也支持 `~`、`～`、`至`、`到`。跨年区间如 `12-30..01-02` 也可以。

---

## 配置

`plugins/maibot-wardrobe/config.toml`：

### `[plugin]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关 |
| `timezone` | `Asia/Shanghai` | 日期计算时区 |
| `config_version` | `2.1.0` | 不要手改 |

### `[identity]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `display_name` | 空 | 留空自动读取 `bot.nickname` |
| `base_appearance` | 示例 | 基础外貌设定，建议改成你自己的角色 |
| `extra_traits` | 空 | 补充特征 |
| `instruction` | 内置 | 告诉模型如何使用这份设定 |

### `[rotation]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `avoid_repeat_days` | `3` | 最近 N 天穿过的衣服尽量不再抽到 |

### `[injection]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否注入提示词 |
| `max_chars` | `600` | 注入文本最大长度 |

### `[vision]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否调用视觉模型识别 |
| `task_name` | `vlm` | 对应 `model_config.toml` 的 vlm 任务 |
| `max_tokens` | `1200` | 视觉模型最大输出 token |
| `prompt` | 内置 | 识别提示词 |
| `description_max_chars` | `200` | 保存描述的最大长度 |

### `[upload]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `max_file_mb` | `16` | 单张图片大小上限 |
| `max_images_per_outfit` | `8` | 每套衣服最多保留张数 |
| `max_total_mb` | `512` | 图片目录总大小上限 |
| `auto_describe` | `true` | 上传后自动识别 |

### `[web_ui]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 启动本地上传页 |
| `port` | `0` | 端口；0 = 自动选择空闲端口 |
| `page_title` | `她的衣橱` | 页面标题 |
| `show_home_card` | `true` | 是否显示 MaiBot 首页卡片 |
| `home_card_order` | `1200` | 首页卡片排序 |

上传页只绑定 `127.0.0.1`，链接带一次性令牌；每次重启端口和令牌会变，请从首页卡片进入。

---

## 数据文件

`data/plugins/github.qipi128-blip.wardrobe/wardrobe.toml`：

```toml
[[outfits]]
id = "spring_breeze"
name = "春日微风"
description = "米白色针织开衫配浅蓝色百褶裙。"
images = []
vision_description = ""

[[events]]
id = "birthday"
name = "生日"
date = "10-28"
outfits = ["birthday_dress"]
note = "今天是生日，穿了白色连衣裙。"
enabled = true
```

网页编辑事件和删除图片时会自动写回这个文件。

---

## 常见问题

**页面提示“连接已断开”或上传 `Failed to fetch`？**

插件重载后旧页面的端口会失效。回 MaiBot 首页重新点「她的衣橱」卡片即可。

**图片太大？**

默认上限 16 MB；超过会直接提示，不会再把连接弄断。可以改 `[upload].max_file_mb`。

**模型不知道她穿什么？**

1. 看 `/衣橱` 是否显示今日穿搭。
2. 确认 `[injection].enabled = true`。
3. 确认 `model_config.toml` 的 `[model_task_config.vlm]` 配了能看图的模型。

**事件没生效？**

- 检查日期写法；
- 事件指定的衣服 ID 必须存在；
- 只有当天命中的事件才会覆盖随机穿搭。

**修改 `wardrobe.toml` 后没生效？**

保存后下一次读取会自动生效；TOML 写错时日志会提示，插件会继续用上一次成功加载的数据。

---

## 测试

```bash
python -m unittest discover -s tests -v
```

## License

MIT
