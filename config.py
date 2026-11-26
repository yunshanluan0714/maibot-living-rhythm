"""Configuration schema for github.yunshanluan0714.living-rhythm v2.0.0."""

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="启用作息状态、QQ 轻互动与网页链接摘要插件")
    config_version: str = Field(default="2.0.0", description="配置版本")


class ScheduleSection(PluginConfigBase):
    __ui_label__ = "昼夜作息与深夜静默"
    __ui_icon__ = "clock"
    __ui_order__ = 1

    enabled: bool = Field(default=True, description="启用昼夜时段划分与深夜硬静默")
    timezone: str = Field(default="Asia/Shanghai", description="作息判定时区")
    sleep_start: str = Field(default="01:00", description="深夜休眠开始时间（HH:MM）")
    sleep_end: str = Field(default="07:30", description="深夜休眠结束时间（HH:MM）")
    auto_adjust_frequency: bool = Field(
        default=True,
        description="进入深夜休眠时自动将各群发言频率调为 0（仅写记忆不烧算力），醒来后恢复",
    )
    block_during_sleep: bool = Field(
        default=True,
        description="深夜休眠时段在出站前兜底拦截非系统通知消息",
    )


class PresenceSection(PluginConfigBase):
    __ui_label__ = "QQ 在线状态与拍一拍"
    __ui_icon__ = "smartphone"
    __ui_order__ = 2

    enabled: bool = Field(default=True, description="按作息时段自动同步 QQ 在线状态（自动探测兼容协议端）")
    private_typing_status: bool = Field(default=True, description="私聊回复前尝试触发「正在输入...」状态")
    poke_back_enabled: bool = Field(default=True, description="清醒时段被拍一拍时按概率自动回戳")
    poke_back_probability: float = Field(default=0.75, ge=0.0, le=1.0, description="被拍后自动回戳的概率")
    poke_back_cooldown_seconds: int = Field(default=60, ge=5, le=3600, description="对同一会话的回戳冷却时间（秒）")
    custom_action_api: str = Field(default="", description="可选：手动指定协议端 action.call API 全名，留空自动探测")


class LinkPreviewSection(PluginConfigBase):
    __ui_label__ = "网页链接自动摘要"
    __ui_icon__ = "link"
    __ui_order__ = 3

    enabled: bool = Field(default=True, description="自动抓取群聊与私聊中的非视频公开网页标题与简介")
    timeout_seconds: float = Field(default=5.0, ge=1.5, le=15.0, description="网页抓取超时秒数")
    max_summary_chars: int = Field(default=320, ge=80, le=800, description="单条网页摘要最大字符数")
    cache_ttl_seconds: int = Field(default=1800, ge=60, le=86400, description="同一链接摘要缓存有效期（秒）")
    skip_domains: list[str] = Field(
        default_factory=lambda: [
            "bilibili.com",
            "b23.tv",
            "douyin.com",
            "v.douyin.com",
            "iesdouyin.com",
        ],
        description="跳过抓取的域名列表（默认跳过 B 站与抖音，避免与专用视频插件重复）",
    )


class LivingRhythmConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    schedule: ScheduleSection = Field(default_factory=ScheduleSection)
    presence: PresenceSection = Field(default_factory=PresenceSection)
    link_preview: LinkPreviewSection = Field(default_factory=LinkPreviewSection)
