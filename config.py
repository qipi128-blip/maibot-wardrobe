"""「她的衣橱」插件的配置模型。

配置分为若干小节：基础开关、形象设定、每日轮换、季节、节日事件、
提示词注入、自拍、图片上传、视觉描述与聊天命令。所有字段都带有默认值，
首次加载时 SDK 会自动生成 ``config.toml``。
"""

from __future__ import annotations

from maibot_sdk import Field, PluginConfigBase

_DEFAULT_APPEARANCE = (
    "十九岁左右的女生，及肩的微卷黑发，圆眼，皮肤白皙，笑起来有浅浅的梨涡；"
    "身形纤细，身高约163cm，气质松弛自然。"
)

_DEFAULT_VISION_PROMPT = (
    "你是一名服装与人物外观描述助手。请用简体中文客观描述这张图片里这位女生的外貌与穿着："
    "发型、脸型与五官给人的感觉、上衣、下装、鞋袜、配饰，以及整体风格和气质。"
    "直接以「她」开头描述，写成一段可以放进角色设定里的文字；不要出现「图中」「图片」「该角色」"
    "「这位女生」之类的指代，不要评价美丑，不要编造图片里没有的细节，不要输出 Markdown，"
    "控制在 120 字以内。"
)

_DEFAULT_IDENTITY_INSTRUCTION = (
    "这是你本人真实的外貌与穿着，不是角色扮演，也不是别人。"
    "当聊天涉及长相、穿搭、天气、季节或节日时，可以自然地按这个设定回答；"
    "不要主动复述整段设定，也不要声称自己没有身体、没有衣服。"
)


class PluginSectionConfig(PluginConfigBase):
    """插件基础开关。"""

    __ui_label__ = "基础设置"
    __ui_icon__ = "settings"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用「她的衣橱」")
    timezone: str = Field(
        default="Asia/Shanghai",
        description="计算日期、季节和每日穿搭使用的时区",
    )
    config_version: str = Field(default="1.1.1", description="配置版本")


class IdentityConfig(PluginConfigBase):
    """麦麦对自己的外貌认知。"""

    __ui_label__ = "形象设定"
    __ui_icon__ = "user-round"
    __ui_order__ = 1

    display_name: str = Field(
        default="",
        description="角色名字；留空时自动读取主程序 bot.nickname",
    )
    base_appearance: str = Field(
        default=_DEFAULT_APPEARANCE,
        description="基础外貌设定（年龄、发型、瞳色、身高、体型、气质等），请改成你自己的角色设定",
    )
    extra_traits: str = Field(
        default="",
        description="补充特征或小习惯，例如「左手腕有一根红绳」，会附在外貌后面",
    )
    instruction: str = Field(
        default=_DEFAULT_IDENTITY_INSTRUCTION,
        description="告诉模型如何使用这份形象设定",
    )


class RotationConfig(PluginConfigBase):
    """每日随机轮换策略。"""

    __ui_label__ = "每日轮换"
    __ui_icon__ = "shuffle"
    __ui_order__ = 2

    enabled: bool = Field(default=True, description="是否启用每日自动换装")
    avoid_repeat_days: int = Field(
        default=7,
        ge=0,
        le=90,
        description="最近多少天内穿过的衣服尽量不再抽到；0 表示不限制",
    )
    prefer_season: bool = Field(
        default=True,
        description="优先从当季衣服里抽取；关闭后从全部衣服里抽取",
    )
    fallback_to_all: bool = Field(
        default=True,
        description="当季没有可用衣服时，是否回退到全部衣服",
    )
    min_pool_size: int = Field(
        default=1,
        ge=1,
        le=20,
        description="去重后候选池至少保留多少套；不足时会放宽去重限制",
    )
    seed: str = Field(
        default="maibot-wardrobe",
        description="随机种子；同一天、同一候选池会得到相同结果，修改后第二天生效",
    )


class SeasonConfig(PluginConfigBase):
    """季节划分。"""

    __ui_label__ = "季节"
    __ui_icon__ = "leaf"
    __ui_order__ = 3

    enabled: bool = Field(default=True, description="是否在提示词和选衣时考虑季节")
    hemisphere: str = Field(
        default="northern",
        description="半球：northern（北半球，默认）或 southern（南半球）",
    )
    ranges: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "自定义季节区间，键为 spring/summer/autumn/winter，值为 "
            "「MM-DD..MM-DD」；留空使用半球默认划分。跨年区间写 "
            "winter = \"12-01..02-28\""
        ),
    )


class EventConfig(PluginConfigBase):
    """特殊日子事件。"""

    __ui_label__ = "特殊日子"
    __ui_icon__ = "calendar-heart"
    __ui_order__ = 4

    enabled: bool = Field(default=True, description="是否启用 wardrobe.toml 里的 [[events]] 事件")
    lookahead_days: int = Field(
        default=30,
        ge=0,
        le=365,
        description="「/衣橱事件」向前预览多少天",
    )
    show_in_prompt: bool = Field(
        default=True,
        description="是否把今天匹配到的事件写进提示词",
    )


class InjectionConfig(PluginConfigBase):
    """提示词注入。"""

    __ui_label__ = "提示词注入"
    __ui_icon__ = "message-square-text"
    __ui_order__ = 5

    enabled: bool = Field(default=True, description="是否把形象与今日穿搭注入模型提示词")
    planner: bool = Field(default=True, description="注入 planner（负责决定行动与调用工具）")
    replyer: bool = Field(default=True, description="注入 replyer（负责生成最终回复）")
    include_appearance: bool = Field(default=True, description="包含基础外貌设定")
    include_outfit: bool = Field(default=True, description="包含今日穿搭")
    include_event: bool = Field(default=True, description="包含今天的特殊事件")
    max_chars: int = Field(
        default=800,
        ge=120,
        le=3000,
        description="每次注入的最大字符数，超出会按句子截断",
    )


class SelfieConfig(PluginConfigBase):
    """自拍与发图。"""

    __ui_label__ = "自拍"
    __ui_icon__ = "camera"
    __ui_order__ = 6

    enabled: bool = Field(default=True, description="是否允许「/看看你」和模型调用自拍工具")
    allow_tool: bool = Field(
        default=True,
        description="是否向模型提供 wardrobe_send_selfie 工具，让她在合适的时候主动发照片",
    )
    caption: str = Field(
        default="{name}今天穿的是「{outfit}」～",
        description="发送图片前的文字；可用变量 {name}、{outfit}、{season}、{event}",
    )
    cooldown_seconds: int = Field(
        default=30,
        ge=0,
        le=3600,
        description="同一聊天里两次发图的最小间隔（秒）",
    )
    session_ttl_seconds: int = Field(
        default=600,
        ge=30,
        le=7200,
        description="模型工具调用时，最近一次聊天会话的有效期（秒）",
    )


class UploadConfig(PluginConfigBase):
    """图片上传。"""

    __ui_label__ = "图片上传"
    __ui_icon__ = "image-plus"
    __ui_order__ = 7

    enabled: bool = Field(default=True, description="是否允许通过聊天命令上传穿搭图")
    allow_all: bool = Field(
        default=False,
        description="是否允许所有聊天用户上传；关闭时仅允许 allowed_user_ids 里的用户",
    )
    allowed_user_ids: list[str] = Field(
        default_factory=list,
        description="允许上传的 QQ 号列表；与 admin_user_ids 同时生效",
    )
    max_file_mb: float = Field(
        default=16.0,
        ge=0.5,
        le=50.0,
        description="单张图片大小上限（MB）；手机截图常见 8-12MB，默认 16",
    )
    max_images_per_outfit: int = Field(
        default=8,
        ge=1,
        le=50,
        description="每套衣服最多保留多少张图片，超出时删除最早上传的",
    )
    max_total_mb: int = Field(
        default=512,
        ge=16,
        le=10240,
        description="整个衣橱图片目录的大小上限（MB）",
    )
    auto_describe: bool = Field(
        default=True,
        description="上传后是否调用视觉模型自动生成外观描述",
    )
    overwrite_description: bool = Field(
        default=False,
        description="上传时附带的文字是否覆盖已有描述；关闭时只补空",
    )


class VisionConfig(PluginConfigBase):
    """视觉模型描述。"""

    __ui_label__ = "视觉描述"
    __ui_icon__ = "eye"
    __ui_order__ = 8

    enabled: bool = Field(default=True, description="是否启用视觉模型生成穿搭描述")
    task_name: str = Field(default="vlm", description="视觉模型的任务名，默认 vlm")
    temperature: float = Field(default=0.3, ge=0.0, le=2.0, description="视觉模型温度")
    max_tokens: int = Field(
        default=1200,
        ge=64,
        le=4096,
        description="视觉模型最大输出 token；部分视觉模型会先输出推理内容，建议不少于 800",
    )
    prompt: str = Field(default=_DEFAULT_VISION_PROMPT, description="视觉描述提示词")
    description_max_chars: int = Field(
        default=200,
        ge=40,
        le=1000,
        description="保存的视觉描述最大字符数",
    )
    max_image_side: int = Field(
        default=1280,
        ge=0,
        le=4096,
        description="生成描述前把图片最长边缩到多少像素；0 表示不缩放（需要 Pillow，未安装时自动跳过）",
    )
    jpeg_quality: int = Field(
        default=85,
        ge=50,
        le=95,
        description="生成描述时重新编码 JPEG 的质量",
    )


class CommandConfig(PluginConfigBase):
    """聊天命令。"""

    __ui_label__ = "聊天命令"
    __ui_icon__ = "terminal"
    __ui_order__ = 9

    enabled: bool = Field(default=True, description="是否启用 /衣橱 系列命令")
    allowed_in_groups: bool = Field(default=True, description="是否允许在群聊使用命令")
    allowed_in_private: bool = Field(default=True, description="是否允许在私聊使用命令")
    admin_user_ids: list[str] = Field(
        default_factory=list,
        description="管理员 QQ 号；可执行换装、预览、重新生成描述等操作",
    )
    max_list_items: int = Field(
        default=30,
        ge=5,
        le=100,
        description="「/衣橱列表」最多显示多少套衣服",
    )


class StorageConfig(PluginConfigBase):
    """数据文件。"""

    __ui_label__ = "数据存储"
    __ui_icon__ = "database"
    __ui_order__ = 10

    wardrobe_file: str = Field(
        default="wardrobe.toml",
        description="衣橱与事件定义文件，相对于插件数据目录",
    )
    state_file: str = Field(
        default="state.json",
        description="每日穿搭状态文件，相对于插件数据目录",
    )


class DebugConfig(PluginConfigBase):
    """调试。"""

    __ui_label__ = "调试"
    __ui_icon__ = "bug"
    __ui_order__ = 11

    simulated_date: str = Field(
        default="",
        description="模拟日期（YYYY-MM-DD）；留空使用真实日期，可用于预览生日/节日穿搭",
    )


class WebUiConfig(PluginConfigBase):
    """本地网页上传。"""

    __ui_label__ = "网页上传"
    __ui_icon__ = "globe"
    __ui_order__ = 12

    enabled: bool = Field(
        default=True,
        description="是否启动只监听 127.0.0.1 的本地上传页，并在 MaiBot 首页显示卡片",
    )
    port: int = Field(
        default=0,
        ge=0,
        le=65535,
        description="上传页端口；0 表示每次启动自动选择空闲端口，固定端口更方便收藏",
    )
    page_title: str = Field(default="她的衣橱", description="上传页标题")
    show_home_card: bool = Field(
        default=True,
        description="是否在 MaiBot 首页显示「她的衣橱」卡片；关闭后仍可用固定端口访问",
    )
    home_card_order: int = Field(
        default=1200,
        ge=0,
        le=99999,
        description="首页卡片排序，越小越靠前",
    )


class WardrobeConfig(PluginConfigBase):
    """插件总配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    rotation: RotationConfig = Field(default_factory=RotationConfig)
    season: SeasonConfig = Field(default_factory=SeasonConfig)
    event: EventConfig = Field(default_factory=EventConfig)
    injection: InjectionConfig = Field(default_factory=InjectionConfig)
    selfie: SelfieConfig = Field(default_factory=SelfieConfig)
    upload: UploadConfig = Field(default_factory=UploadConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    commands: CommandConfig = Field(default_factory=CommandConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    web_ui: WebUiConfig = Field(default_factory=WebUiConfig)
