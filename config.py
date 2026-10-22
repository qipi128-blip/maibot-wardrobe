"""「她的衣橱」简化版配置：只保留形象、随机穿搭、事件、视觉识别和网页。"""

from __future__ import annotations

from maibot_sdk import Field, PluginConfigBase

_DEFAULT_APPEARANCE = (
    "十九岁左右的女生，及肩的微卷黑发，圆眼，皮肤白皙，笑起来有浅浅的梨涡；"
    "身形纤细，身高约163cm，气质松弛自然。"
)

_DEFAULT_VISION_PROMPT = (
    "你是一名服装与人物外观描述助手。请用简体中文客观描述这张图片里这位女生的外貌与穿着："
    "发型、脸型与五官给人的感觉、上衣、下装、鞋袜、配饰，以及整体风格。"
    "直接以「她」开头描述，写成一段可以放进角色设定里的文字；不要出现「图中」「图片」「该角色」"
    "「这位女生」之类的指代，不要评价美丑，不要编造图片里没有的细节，不要输出 Markdown，"
    "控制在 120 字以内。"
)

_DEFAULT_IDENTITY_INSTRUCTION = (
    "这是你本人真实的外貌与穿着，不是角色扮演，也不是别人。"
    "当聊天涉及长相、穿搭或今天穿了什么时，自然地按这个设定回答；"
    "不要主动复述整段设定，也不要声称自己没有身体、没有衣服。"
)


class PluginSectionConfig(PluginConfigBase):
    """基础开关。"""

    __ui_label__ = "基础设置"
    __ui_icon__ = "settings"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用「她的衣橱」")
    timezone: str = Field(default="Asia/Shanghai", description="计算日期使用的时区")
    config_version: str = Field(default="2.1.0", description="配置版本")


class IdentityConfig(PluginConfigBase):
    """麦麦对自己的认知。"""

    __ui_label__ = "形象设定"
    __ui_icon__ = "user-round"
    __ui_order__ = 1

    display_name: str = Field(default="", description="角色名字；留空自动读取 bot.nickname")
    base_appearance: str = Field(default=_DEFAULT_APPEARANCE, description="基础外貌设定，请改成你自己的角色")
    extra_traits: str = Field(default="", description="补充特征，例如「左手腕有一根红绳」")
    instruction: str = Field(default=_DEFAULT_IDENTITY_INSTRUCTION, description="告诉模型如何使用这份设定")


class RotationConfig(PluginConfigBase):
    """每日随机穿搭。"""

    __ui_label__ = "每日随机"
    __ui_icon__ = "shuffle"
    __ui_order__ = 2

    avoid_repeat_days: int = Field(
        default=3,
        ge=0,
        le=30,
        description="最近多少天穿过的衣服尽量不再抽到；0 表示不限制",
    )


class InjectionConfig(PluginConfigBase):
    """提示词注入。"""

    __ui_label__ = "提示词注入"
    __ui_icon__ = "message-square-text"
    __ui_order__ = 3

    enabled: bool = Field(default=True, description="是否把形象和今日穿搭注入模型提示词")
    max_chars: int = Field(default=600, ge=120, le=2000, description="每次注入的最大字符数")


class VisionConfig(PluginConfigBase):
    """视觉模型识别穿搭。"""

    __ui_label__ = "视觉识别"
    __ui_icon__ = "eye"
    __ui_order__ = 4

    enabled: bool = Field(default=True, description="是否在图片上传后调用视觉模型生成描述")
    task_name: str = Field(default="vlm", description="视觉模型任务名，默认 vlm")
    max_tokens: int = Field(
        default=1200,
        ge=64,
        le=4096,
        description="视觉模型最大输出 token；部分模型会先输出推理内容，建议不少于 800",
    )
    prompt: str = Field(default=_DEFAULT_VISION_PROMPT, description="视觉识别提示词")
    description_max_chars: int = Field(default=200, ge=40, le=1000, description="保存的描述最大字符数")


class UploadConfig(PluginConfigBase):
    """网页上传。"""

    __ui_label__ = "图片上传"
    __ui_icon__ = "image-plus"
    __ui_order__ = 5

    max_file_mb: float = Field(default=16.0, ge=0.5, le=50.0, description="单张图片大小上限（MB）")
    max_images_per_outfit: int = Field(
        default=8,
        ge=1,
        le=50,
        description="每套衣服最多保留多少张图，超出时删除最早上传的",
    )
    max_total_mb: int = Field(default=512, ge=16, le=10240, description="整个图片目录的大小上限（MB）")
    auto_describe: bool = Field(default=True, description="上传后是否自动调用视觉模型识别穿搭")


class WebUiConfig(PluginConfigBase):
    """本地上传页。"""

    __ui_label__ = "网页上传"
    __ui_icon__ = "globe"
    __ui_order__ = 6

    enabled: bool = Field(default=True, description="启动只监听 127.0.0.1 的本地页面")
    port: int = Field(default=0, ge=0, le=65535, description="端口；0 表示每次启动自动选择空闲端口")
    page_title: str = Field(default="她的衣橱", description="页面标题")
    show_home_card: bool = Field(default=True, description="是否在 MaiBot 首页显示「她的衣橱」卡片")
    home_card_order: int = Field(default=1200, ge=0, le=99999, description="首页卡片排序")


class WardrobeConfig(PluginConfigBase):
    """插件总配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    rotation: RotationConfig = Field(default_factory=RotationConfig)
    injection: InjectionConfig = Field(default_factory=InjectionConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    upload: UploadConfig = Field(default_factory=UploadConfig)
    web_ui: WebUiConfig = Field(default_factory=WebUiConfig)
