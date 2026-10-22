# 她的衣橱 · MaiBot Wardrobe

让麦麦知道**自己长什么样、今天穿了什么**的 MaiBot 插件。

上传一张（或多张）穿搭图，插件会调用视觉模型把图片转成文字描述，再连同基础外貌设定、季节和特殊日子一起注入 planner / replyer 的系统提示词。即使回复模型本身不支持看图，她也能自然地回答“你今天穿什么”“你长什么样”，并且每天自动换一套衣服。

> 适用：MaiBot 1.2.0+ / 插件 SDK 2.8.0+，建议配合 Napcat 适配器使用。

---

## 功能

- **形象档案**：年龄、发型、瞳色、身高、体型、气质，以及补充特征（比如手腕上的红绳）。
- **衣橱与图片**：每套衣服可以有多张图；`/衣橱上传` 支持带图发送或回复图片，自动去重、限制大小、按套数清理旧图。
- **视觉描述**：上传后自动调用 `vlm` 任务生成客观的穿搭描述；描述会写回 `wardrobe.toml`，也可以手动改。
- **每天随机换装**：同一天结果稳定（重启不变），第二天自动换；支持权重、最近 N 天不重复、当季优先。
- **季节**：北半球 / 南半球 / 自定义四个季节的日期区间；衣服可以标注只在某些季节穿。
- **特殊日子**：每年固定日期、绝对日期、日期区间（支持跨年）、农历（春节/中秋等）、星期、月份、季节；可指定几套衣服随机抽，也可以只写一句说明。
- **提示词注入**：同时注入 planner 和 replyer；如果模型不是视觉模型也没关系，靠文字描述就能“知道自己”。
- **自拍**：`/看看你` 直接发今天的照片；同时提供 `wardrobe_send_selfie` 工具，让模型在合适的时候自己发。
- **预览与调试**：`/衣橱预览 2026-10-28` 看某天会穿什么；`debug.simulated_date` 可以临时把插件日期固定到某一天。
- **热更新**：改 `wardrobe.toml` 或 `config.toml` 后不需要重启 MaiBot（配置由主程序热重载）。

---

## 安装

### 插件市场

在 MaiBot WebUI 的插件中心搜索「她的衣橱」安装即可。首次安装会自动安装可选依赖 `lunardate`（用于农历节日）。

### 手动安装

1. 把仓库放到 `MaiBot/plugins/maibot-wardrobe/`。
2. 如果环境里没有 `lunardate`，执行：
   ```bash
   uv pip install "lunardate>=0.2.2"
   # 或 python -m pip install "lunardate>=0.2.2"
   ```
3. 重启 MaiBot，或在 WebUI 插件页重新加载。
4. 首次加载会在插件目录生成 `config.toml`，并在 `MaiBot/data/plugins/github.qipi128-blip.wardrobe/` 生成 `wardrobe.toml` 和 `state.json`。

---

## 快速开始

在 QQ 里（私聊或群聊）发送：

```text
/衣橱上传 daily_hoodie 日常卫衣
```

把这句话和一张图一起发送，或者回复一张图再发送。插件会：

1. 新建/更新 `daily_hoodie` 这套衣服；
2. 把图片存到 `data/plugins/github.qipi128-blip.wardrobe/outfits/daily_hoodie/`；
3. 调用视觉模型生成描述；
4. 下一次聊天时，麦麦就知道自己今天穿的是这套。

然后：

```text
/衣橱             # 今天的穿搭、季节、特殊日子
/看看你           # 发今天的照片
/衣橱列表         # 所有衣服
/衣橱事件         # 今天和未来的特殊日子
/衣橱预览 2026-10-28   # 管理员：预览生日当天穿什么
```

---

## 它是怎么让模型“知道自己”的

1. 基础外貌来自 `config.toml` 的 `[identity]`。
2. 衣服的文字描述来自 `wardrobe.toml` 的 `description` 和视觉模型生成的 `vision_description`。
3. 插件在 `maisaka.planner.before_request` 和 `maisaka.replyer.before_model_request` 两个 Hook 里，把一段 `【你此刻的形象｜日期 星期 季节】` 追加到**系统提示词**。
4. 因此回复模型不需要支持图片；只要视觉模型能把图描述清楚，文字模型就能理解。

示例注入内容：

```text
【你此刻的形象｜2026-10-28 星期三 · 秋天】
你的名字：麦麦。
你的样子：十九岁左右的女生，及肩的微卷黑发，圆眼……
你今天的穿着：「生日礼服」——白色方领连衣裙，腰间系一条细缎带……
今天的特别事件：生日。今天是你的生日，你特意换上了最喜欢的白色连衣裙……
这是你本人真实的外貌与穿着，不是角色扮演……
```

> 如果回复模型本身支持视觉，也不需要额外配置：插件仍然用文字描述，稳定、便宜，也不依赖模型的多模态能力。

---

## 配置

配置文件：`MaiBot/plugins/maibot-wardrobe/config.toml`（由 SDK 生成，可在 WebUI 插件配置页编辑）。

### `[plugin]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关 |
| `timezone` | `Asia/Shanghai` | 日期/季节计算时区 |
| `config_version` | `1.0.0` | 不要手改 |

### `[identity]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `display_name` | 空 | 留空自动读取 `bot.nickname` |
| `base_appearance` | 示例 | **建议改成你自己的角色设定** |
| `extra_traits` | 空 | 补充特征 |
| `instruction` | 内置 | 告诉模型如何使用这份设定 |

### `[rotation]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 每日换装 |
| `avoid_repeat_days` | `7` | 最近 N 天不重复；0 关闭 |
| `prefer_season` | `true` | 当季优先 |
| `fallback_to_all` | `true` | 当季没有衣服时回退全部 |
| `min_pool_size` | `1` | 去重后至少保留多少套 |
| `seed` | `maibot-wardrobe` | 随机种子 |

### `[season]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否考虑季节 |
| `hemisphere` | `northern` | `northern` / `southern` |
| `ranges` | 空 | 自定义区间，例如 `{ spring = "02-01..04-30" }` |

不填 `ranges` 时使用气象学划分：3-5 春、6-8 夏、9-11 秋、12-2 冬。

### `[event]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用事件 |
| `lookahead_days` | `30` | `/衣橱事件` 预览天数 |
| `show_in_prompt` | `true` | 是否把事件写进提示词 |

### `[injection]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关 |
| `planner` / `replyer` | `true` | 分别注入 |
| `include_appearance` / `include_outfit` / `include_event` | `true` | 包含哪些内容 |
| `max_chars` | `800` | 注入文本最大长度 |

### `[selfie]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否允许发图 |
| `allow_tool` | `true` | 是否把 `wardrobe_send_selfie` 工具交给模型 |
| `caption` | `{name}今天穿的是「{outfit}」～` | 文案模板，支持 `{name}` `{outfit}` `{season}` `{event}` |
| `cooldown_seconds` | `30` | 同一聊天发图冷却 |
| `session_ttl_seconds` | `600` | 工具调用时最近会话的有效期 |

### `[upload]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否允许上传 |
| `allow_all` | `false` | 是否允许所有人上传 |
| `allowed_user_ids` | `[]` | 允许上传的 QQ 号 |
| `max_file_mb` | `8` | 单图上限 |
| `max_images_per_outfit` | `8` | 每套保留张数 |
| `max_total_mb` | `512` | 图片目录总上限 |
| `auto_describe` | `true` | 上传后自动视觉描述 |
| `overwrite_description` | `false` | 上传文字是否覆盖已有描述 |

> 第一次使用前，请把自己的 QQ 号加入 `allowed_user_ids`，或临时把 `allow_all` 设为 `true`。权限同时接受 `[commands].admin_user_ids`。

### `[vision]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用视觉描述 |
| `task_name` | `vlm` | 对应 `model_config.toml` 的 `[model_task_config.vlm]` |
| `prompt` | 内置 | 视觉描述提示词 |
| `description_max_chars` | `200` | 保存长度 |

### `[commands]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 命令总开关 |
| `allowed_in_groups` / `allowed_in_private` | `true` | 群聊/私聊开关 |
| `admin_user_ids` | `[]` | 管理员 QQ，可换装/预览/重新描述 |
| `max_list_items` | `30` | 列表最多显示条数 |

### `[debug]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `simulated_date` | 空 | `YYYY-MM-DD`，临时模拟日期；留空用真实日期 |

---

## 衣橱文件：`wardrobe.toml`

位置：`MaiBot/data/plugins/github.qipi128-blip.wardrobe/wardrobe.toml`。

### 一套衣服

```toml
[[outfits]]
id = "spring_breeze"          # 必填，字母/数字/下划线/短横线，最多 48 字符
name = "春日微风"
description = "米白色针织开衫配浅蓝色百褶裙，白色短袜和棕色乐福鞋。"  # 给模型看
tags = ["清新", "春日"]
seasons = ["spring"]          # 留空 = 四季通用
weight = 1.0                  # 抽取权重，越大越容易被抽到
enabled = true
only_for_events = false       # true = 只在特殊日子穿，不参与日常轮换
images = []                   # 上传命令自动维护
vision_description = ""       # 视觉模型自动生成
prompt_extra = ""             # 额外提示，例如“今天头发有点乱”
```

### 一个特殊日子

```toml
[[events]]
id = "birthday"
name = "生日"
enabled = true
priority = 200                # 越大越优先；同优先级会合并
dates = ["10-28"]             # 每年重复；也可以写 "2026-10-28"
ranges = ["12-24..12-26"]     # 区间；支持跨年，如 "12-30..01-02"
lunar_dates = ["08-15"]       # 农历；闰月写 "闰04-01"；需要 lunardate
weekdays = [5]                # 可选：1=周一 … 7=周日
months = [10]                 # 可选：只在 10 月
seasons = ["winter"]          # 可选：只在冬天
outfits = ["birthday_dress"]  # 指定几套，从中随机抽
note = "今天是你的生日，你特意换上了白色连衣裙。"
exclusive = true              # true = 只从 outfits 里选
```

### 匹配规则

- `dates` / `ranges` / `lunar_dates` 之间是**或**关系。
- `weekdays` / `months` / `seasons` 是额外的**与**过滤。
- 一个事件至少有日期条件或过滤条件才会生效。
- 事件没写 `outfits` 时，只把 `note` 写进提示词，衣服仍按季节轮换。
- `only_for_events = true` 的衣服只会在事件里出现。

### 常用例子

**整个冬天都穿大衣（季节事件）**

```toml
[[events]]
id = "winter_coat"
name = "冬天"
priority = 40
seasons = ["winter"]
outfits = ["winter_snow", "autumn_latte"]
note = "冬天出门会穿厚一点。"
```

**每周五穿休闲装**

```toml
[[events]]
id = "friday_casual"
name = "周五"
priority = 20
weekdays = [5]
outfits = ["daily_hoodie"]
```

**春节（农历）**

```toml
[[events]]
id = "spring_festival"
name = "春节"
priority = 180
lunar_dates = ["01-01"]
outfits = ["new_year_hanfu", "birthday_dress"]
note = "今天是春节，穿得喜庆一点。"
```

**只有说明、不换衣服**

```toml
[[events]]
id = "exam_week"
name = "考试周"
priority = 10
ranges = ["06-20..06-26"]
note = "这周在准备考试，可能回消息慢一点。"
```

---

## 命令

| 命令 | 权限 | 说明 |
| --- | --- | --- |
| `/衣橱`、`/衣橱状态` | 所有人 | 今日穿搭、季节、事件、图片数 |
| `/衣橱列表` | 所有人 | 所有衣服 |
| `/衣橱帮助` | 所有人 | 命令帮助 |
| `/衣橱上传 <id> <名字>` | `upload` 白名单 | 上传图片并新建/补充衣服 |
| `/衣橱描述 <id>` | 管理员 | 用视觉模型重新生成描述 |
| `/衣橱换装` | 管理员 | 重新抽今天的衣服 |
| `/衣橱事件` | 所有人 | 今天 + 未来事件 |
| `/衣橱预览 <YYYY-MM-DD>` | 管理员 | 预览某天穿什么 |
| `/看看你`、`/自拍` | 所有人 | 发今天的照片 |

## 模型工具

`wardrobe_send_selfie`：模型可以在用户想看照片、聊到穿搭时主动调用，把今天的图片发到当前聊天。受 `[selfie].allow_tool`、冷却和会话有效期限制。

---

## 数据与隐私

- 图片只保存在本机 `MaiBot/data/plugins/github.qipi128-blip.wardrobe/outfits/`。
- 只有生成视觉描述时，图片会以 base64 发送给**你自己配置的视觉模型**；不生成描述就不会上传。
- 可以用 `/衣橱上传` 覆盖图片，或直接删除 `outfits/<id>/` 下的文件并清空 `images` 字段。
- 插件不读取私聊以外的内容，也不把聊天记录发给第三方。

## 排查

**模型好像不知道自己的穿搭？**

1. 看 `/衣橱状态` 是否正常输出了今日穿搭。
2. 确认 `[injection].enabled = true`，且 `planner`/`replyer` 至少一个为 `true`。
3. 当前 MaiBot 需要支持 `maisaka.planner.before_request` / `maisaka.replyer.before_model_request` 两个 Hook（1.2.x 已支持）。如果日志里没有“衣橱提示词注入失败”，一般就是生效了。
4. 模型可能选择不复述设定；问它“你今天穿的什么”测试。

**上传后没有自动描述？**

- 确认 `[upload].auto_describe = true`、`[vision].enabled = true`。
- 确认 `model_config.toml` 里 `[model_task_config.vlm]` 配了能看图的模型。
- 先 `/衣橱描述 <id>` 手动重试，错误会直接回复。

**农历事件不生效？**

- 执行 `python -m pip install "lunardate>=0.2.2"`，或让插件市场自动安装依赖。
- `/衣橱事件` 会提示是否缺少 `lunardate`。

**上传提示没有权限？**

- 把你的 QQ 号加入 `[upload].allowed_user_ids`，或把 `allow_all` 设为 `true`。
- 管理员命令还需要加入 `[commands].admin_user_ids`。

**改了 `wardrobe.toml` 没生效？**

- 保存后下一次聊天/命令会自动重新读取；如果 TOML 写错，日志会提示，插件会继续使用上一次成功加载的数据。

---

## 开发与测试

```bash
cd maibot-wardrobe
python -m unittest discover -s tests -v
```

测试覆盖季节、日期区间、农历、优先级、去重、稳定随机、提示词注入、上传、权限、自拍工具和视觉描述。

## 更新日志

见 [CHANGELOG.md](./CHANGELOG.md)。

## License

MIT
