# `maibot-living-rhythm` (v2.0.1) — 作息状态、QQ 轻互动与网页链接摘要

面向**原生零第三方依赖** MaiBot（`1.3.19`–`1.4.99`，SDK `2.20.0`–`2.99.99`）的轻量化实用扩展。**常态清醒时段 0 Prompt 注入**、无需改动宿主源码，专注 3 件真实好用的事：

## 核心功能

1. **昼夜作息联动 QQ 在线状态 + 深夜硬静默**
   - 按作息时段（默认 `01:00–07:30` 为深夜休眠，`Asia/Shanghai`）自动通过 `set_online_status` 切换 QQ 在线状态（睡觉中、在线、学习中、干饭中、听歌中、离开）。
   - 进入深夜休眠后自动调用原生 `frequency.set_adjust(0.0)` 静默收消息不烧算力，并在 `send_service.before_send` 兜底拦截非系统通知消息（放行 `【防撤回提醒】` 等系统级通知），醒来后自动恢复。
2. **私聊正在输入状态 + 清醒时段拍一拍回戳**
   - 通过 `ctx.api.list()` 自动探测 OneBot / NapCat / SnowLuma 协议端，无须硬编码适配器 ID，不支持的协议动作自动静默跳过。
   - 私聊即将回复时触发 `set_input_status` 显示「正在输入...」；清醒时段收到私聊或群内点名拍一拍按概率与冷却调用 `send_poke` 回戳。
3. **非视频网页链接自动摘要**
   - 自动抓取群聊/私聊中公众号、资讯、文章等公开网页的标题与简介，拼入入站文本方便主脑直接看懂链接内容。
   - 默认跳过 `bilibili.com`、`b23.tv`、`douyin.com` 等视频站点（可在配置 `skip_domains` 自定义），内置私网地址 SSRF 拦截与内存 TTL 缓存。

## 安装与配置

1. 在 MaiBot 插件市场安装本插件（`github.yunshanluan0714.living-rhythm`）并重启或热加载。
2. 在 WebUI 插件管理页按需调整作息时间段、回戳概率/冷却与跳过抓取域名。

## 运行单元测试

```bash
python -m unittest discover -p "test_*.py"
```

## 开源协议

本项目基于 [MIT License](LICENSE) 开源。
