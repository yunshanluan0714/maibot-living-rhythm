"""Configuration schema for github.yunshanluan0714.living-rhythm."""

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="启用拟人作息、现实锚点与主动社交插件")
    config_version: str = Field(default="1.0.0", description="配置版本")


class ScheduleSection(PluginConfigBase):
    __ui_label__ = "昼夜作息与爬楼"
    __ui_icon__ = "clock"
    __ui_order__ = 1

    enabled: bool = Field(default=True, description="启用昼夜作息状态机与深夜休眠")
    timezone: str = Field(default="Asia/Shanghai", description="作息判定时区")
    sleep_start: str = Field(default="01:00", description="深夜休眠开始时间（HH:MM）")
    sleep_end: str = Field(default="07:30", description="深夜休眠结束时间（HH:MM）")
    auto_adjust_frequency: bool = Field(
        default=True,
        description="进入深夜休眠时自动将各群发言频率调为 0（仅写记忆不烧算力），醒来后恢复",
    )
    wake_catchup_enabled: bool = Field(default=True, description="睡醒后选择性爬楼回复休眠期间高价值未读消息")
    wake_catchup_max_per_group: int = Field(default=2, ge=0, le=5, description="睡醒后每个群最多主动爬楼补回的消息条数")


class PresenceSection(PluginConfigBase):
    __ui_label__ = "在线状态与输入态"
    __ui_icon__ = "smartphone"
    __ui_order__ = 2

    enabled: bool = Field(default=True, description="配合虚拟作息自动同步 QQ 在线状态（若宿主有兼容适配器）")
    sync_device_model: bool = Field(default=False, description="按白天电脑/晚上手机切换设备标识（部分客户端不支持时自动跳过）")
    day_device_name: str = Field(default="Windows PC", description="白天在线设备标识")
    night_device_name: str = Field(default="iPhone", description="夜间在线设备标识")
    private_typing_status: bool = Field(default=True, description="私聊回复前尝试触发「正在输入...」状态")
    custom_action_api: str = Field(default="", description="可选：手动指定适配器 action.call API 全名，留空自动探测")


class MicroActionsSection(PluginConfigBase):
    __ui_label__ = "群接龙与群投票"
    __ui_icon__ = "gift"
    __ui_order__ = 3

    enabled: bool = Field(default=True, description="启用群接龙与群投票感知互动")
    solitaire_enabled: bool = Field(default=True, description="识别群 #接龙 并按需参与跟队")
    poll_enabled: bool = Field(default=True, description="识别群投票并顺口表态互动")


class RealityAnchorSection(PluginConfigBase):
    __ui_label__ = "现实锚点绑定"
    __ui_icon__ = "cloud-rain"
    __ui_order__ = 4

    enabled: bool = Field(default=True, description="绑定城市实时天气、中国节假日、调休与节点")
    city: str = Field(default="上海", description="绑定城市名（如 上海/北京/杭州/广州，留空则不抓天气）")
    province: str = Field(default="", description="所在省份/直辖市名（常见城市留空可自动推断）")
    weather_refresh_hours: float = Field(default=2.0, ge=0.5, le=12.0, description="实时天气拉取间隔（小时）")
    adjusted_workdays: list[str] = Field(default_factory=list, description="调休补班日期列表（YYYY-MM-DD）")
    extra_holidays: list[str] = Field(default_factory=list, description="额外法定节假日日期列表（YYYY-MM-DD）")


class CircleRadarSection(PluginConfigBase):
    __ui_label__ = "圈层动态低频雷达"
    __ui_icon__ = "radio"
    __ui_order__ = 5

    enabled: bool = Field(default=True, description="低频抓取兴趣标签公开热搜与分区榜单，融入聊天与冷群破冰")
    interest_tags: list[str] = Field(
        default_factory=lambda: ["游戏", "科技", "AI", "动画", "电影"],
        description="兴趣标签列表（匹配公开榜单关键词）",
    )
    refresh_hours: float = Field(default=4.0, ge=1.0, le=24.0, description="抓取间隔（小时）")
    max_items: int = Field(default=8, ge=2, le=20, description="本地保留的最新圈层动态条数")


class ProactiveSection(PluginConfigBase):
    __ui_label__ = "冷群破冰与主动互动"
    __ui_icon__ = "sparkles"
    __ui_order__ = 6

    enabled: bool = Field(default=True, description="启用冷群破冰、被拍自动回戳与可选熟人轻私聊")
    cold_chat_idle_minutes: int = Field(default=180, ge=30, le=1440, description="群聊安静超过多少分钟后视为冷场")
    cold_chat_cooldown_hours: float = Field(default=6.0, ge=1.0, le=48.0, description="同一群两次冷场破冰最小间隔（小时）")
    cold_chat_daily_limit: int = Field(default=2, ge=0, le=10, description="单个群每天最多主动冷场破冰次数")
    poke_back_enabled: bool = Field(default=True, description="清醒时段被拍一拍时按概率自动回戳（需适配器支持 send_poke）")
    poke_back_probability: float = Field(default=0.75, ge=0.0, le=1.0, description="被拍后直接调用回戳的概率")
    poke_back_cooldown_seconds: int = Field(default=60, ge=5, le=3600, description="对同一会话回戳冷却时间（秒）")
    private_proactive_enabled: bool = Field(default=False, description="允许在特定活跃节点主动向白名单熟人发轻私聊")
    private_whitelist_users: list[str] = Field(
        default_factory=list,
        description="允许主动发起私聊的熟人账号 ID 白名单（留空则不自动找任何人私聊）",
    )
    private_cooldown_hours: float = Field(default=24.0, ge=1.0, le=168.0, description="对同一熟人两次主动私聊最小间隔（小时）")


class LivingRhythmConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    schedule: ScheduleSection = Field(default_factory=ScheduleSection)
    presence: PresenceSection = Field(default_factory=PresenceSection)
    micro_actions: MicroActionsSection = Field(default_factory=MicroActionsSection)
    reality_anchor: RealityAnchorSection = Field(default_factory=RealityAnchorSection)
    circle_radar: CircleRadarSection = Field(default_factory=CircleRadarSection)
    proactive: ProactiveSection = Field(default_factory=ProactiveSection)
