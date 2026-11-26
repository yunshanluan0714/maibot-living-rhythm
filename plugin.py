"""Main plugin wiring for github.yunshanluan0714.living-rhythm v2.0.1."""

from __future__ import annotations

import asyncio
from datetime import datetime
import random
import re
import time
from typing import Any
from uuid import uuid4

from maibot_sdk import HookHandler, MaiBotPlugin
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

from .adapter_bridge import DynamicAdapterBridge
from .config import LivingRhythmConfig
from .link_preview import LinkPreviewCache, extract_previewable_urls, fetch_url_summary_sync
from .micro_actions import resolve_presence_for_phase
from .schedule_engine import resolve_schedule_state

_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
_HEADER_RE = re.compile(r"^\s*<message\b([^>]*)>", re.S)
_POKE_TEXT_RE = re.compile(r"戳了戳|发起了戳一戳")
_SYSTEM_NOTICE_RE = re.compile(
    r"^(?:【[^】]{1,20}(?:提醒|通知|播报|报告|告警|警告|状态|结果|防撤回)】|\[系统通知\])"
)


def is_system_notification_text(text: str) -> bool:
    """Identify structured plugin/system notifications (e.g. anti-recall) so sleep guard never blocks them."""
    clean = str(text or "").strip()
    return bool(clean and _SYSTEM_NOTICE_RE.match(clean))


def _extract_components(msg: dict[str, Any]) -> list[dict[str, Any]]:
    raw = msg.get("raw_message")
    if isinstance(raw, list):
        return [c for c in raw if isinstance(c, dict)]
    if isinstance(raw, dict):
        comps = raw.get("components")
        if isinstance(comps, list):
            return [c for c in comps if isinstance(c, dict)]
    return []


def _extract_last_user_from_items(items: list[dict[str, Any]]) -> str:
    for item in reversed(items):
        if not isinstance(item, dict) or item.get("item_type") != "UserMessageItem":
            continue
        texts = [
            str(p.get("text") or "")
            for p in item.get("parts") or []
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        if not texts:
            continue
        match = _HEADER_RE.match(texts[0])
        if match is None:
            continue
        attrs = dict(_ATTR_RE.findall(match.group(1)))
        if attrs.get("is_self_message", "").lower() == "true":
            continue
        return attrs.get("user_id", "").strip()
    return ""


def _build_reminder_item(text: str) -> dict[str, Any]:
    return {
        "item_type": "UserMessageItem",
        "meta": {"item_id": uuid4().hex, "logical_turn_id": None, "timestamp": datetime.now().isoformat()},
        "parts": [{"type": "text", "text": text}],
    }


def _inject_reminder(items: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    updated = list(items)
    pos = len(updated)
    if updated and updated[-1].get("item_type") == "UserMessageItem":
        texts = [str(p.get("text") or "") for p in updated[-1].get("parts") or [] if isinstance(p, dict)]
        if texts and _HEADER_RE.match(texts[0]) is None:
            pos -= 1
    updated.insert(pos, _build_reminder_item(text))
    return updated


class LivingRhythmPlugin(MaiBotPlugin):
    """作息状态同步、深夜硬静默、QQ 轻互动与网页链接摘要插件。"""

    config_model = LivingRhythmConfig

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._rng = random.Random()
        self._bridge = DynamicAdapterBridge()
        self._link_cache = LinkPreviewCache()
        self._loop_task: asyncio.Task[None] | None = None
        self._bot_account = ""
        self._bot_nickname = ""
        self._bot_aliases: list[str] = []
        self._session_to_group: dict[str, str] = {}
        self._session_to_private: dict[str, str] = {}
        self._recent_senders: dict[str, str] = {}
        self._last_poke_back: dict[str, float] = {}
        self._last_typing_ping: dict[str, float] = {}
        self._last_synced_presence_phase: str = ""
        self._sleep_freq_applied: bool | None = None
        self._muted_sessions: dict[str, float] = {}

    async def on_load(self) -> None:
        self._apply_config_to_engines()
        await self._refresh_bot_identity()
        await self._warm_stream_mappings()
        self._running = True
        self._loop_task = asyncio.create_task(self._run_scheduler_loop(), name="living-rhythm-loop")
        self.ctx.logger.info("作息状态、QQ 轻互动与网页摘要插件 v2.0.1 已加载")

    async def on_unload(self) -> None:
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None
        if self._muted_sessions:
            await self._restore_muted_sessions()

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        del scope, config_data, version
        self._apply_config_to_engines()
        self._last_synced_presence_phase = ""
        await self._refresh_bot_identity()
        if self._muted_sessions and (
            not self.config.plugin.enabled
            or not self.config.schedule.enabled
            or not self.config.schedule.auto_adjust_frequency
        ):
            restored = await self._restore_muted_sessions()
            if restored:
                self._sleep_freq_applied = False

    def _apply_config_to_engines(self) -> None:
        cfg = self.config
        self._bridge.set_override(cfg.presence.custom_action_api)
        self._link_cache.ttl_seconds = cfg.link_preview.cache_ttl_seconds

    async def _refresh_bot_identity(self) -> None:
        try:
            qq = await self.ctx.config.get("bot.qq_account", "")
            nick = await self.ctx.config.get("bot.nickname", "")
            aliases = await self.ctx.config.get("bot.alias_names", [])
            if isinstance(qq, str):
                self._bot_account = qq.strip()
            if isinstance(nick, str):
                self._bot_nickname = nick.strip()
            if isinstance(aliases, list):
                self._bot_aliases = [str(a).strip() for a in aliases if str(a).strip()]
        except Exception:
            pass

    def _all_aliases(self) -> list[str]:
        return [x for x in [self._bot_nickname, *self._bot_aliases] if x]

    def _current_schedule(self) -> Any:
        cfg = self.config
        return resolve_schedule_state(
            timezone_name=cfg.schedule.timezone,
            sleep_start=cfg.schedule.sleep_start,
            sleep_end=cfg.schedule.sleep_end,
            enabled=bool(cfg.plugin.enabled and cfg.schedule.enabled),
        )

    async def _warm_stream_mappings(self) -> None:
        try:
            getter = getattr(self.ctx.chat, "get_all_streams", None) or getattr(self.ctx.chat, "get_group_streams", None)
            if getter is None:
                return
            raw = await getter(platform="qq")
            streams = raw.get("streams", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
            if not isinstance(streams, list):
                return
            for st in streams:
                if not isinstance(st, dict):
                    continue
                sid = str(st.get("session_id") or st.get("stream_id") or "").strip()
                if not sid:
                    continue
                gid = str(st.get("group_id") or "").strip()
                uid = str(st.get("user_id") or "").strip()
                if st.get("is_group_session") or st.get("chat_type") == "group" or gid:
                    if gid:
                        self._session_to_group[sid] = gid
                else:
                    self._session_to_group.setdefault(sid, "")
                    if uid:
                        self._session_to_private[sid] = uid
        except Exception:
            pass

    def _resolve_session_context(self, session_id: str) -> tuple[bool, str, str]:
        sid = str(session_id or "").strip()
        if sid in self._session_to_group:
            gid = self._session_to_group[sid]
            return bool(gid), gid, self._session_to_private.get(sid, "")
        if ":group:" in sid:
            gid = sid.split(":group:")[-1]
            return True, gid, ""
        if ":user:" in sid:
            uid = sid.split(":user:")[-1]
            return False, "", uid
        return False, "", self._session_to_private.get(sid, "")

    @HookHandler(
        "chat.receive.after_process",
        name="living_rhythm_receive_after_process",
        description="维护会话映射、清醒时段拍一拍自动回戳与非视频网页链接摘要抓取",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        timeout_ms=6000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_receive_after_process(self, **kwargs: Any) -> dict[str, Any]:
        unchanged = {"action": "continue", "modified_kwargs": kwargs}
        if not self._running or not self.config.plugin.enabled:
            return unchanged
        msg = kwargs.get("message")
        if not isinstance(msg, dict):
            return unchanged

        msg_info = msg.get("message_info") if isinstance(msg.get("message_info"), dict) else {}
        user_info = msg_info.get("user_info") if isinstance(msg_info.get("user_info"), dict) else {}
        group_info = msg_info.get("group_info") if isinstance(msg_info.get("group_info"), dict) else None
        user_id = str(user_info.get("user_id") or "").strip()
        group_id = str(group_info.get("group_id") or "").strip() if group_info else ""
        session_id = str(msg.get("session_id") or "").strip()
        platform = str(msg_info.get("platform") or "qq").strip()
        if not session_id and platform:
            session_id = f"{platform}:{'group:' + group_id if group_id else 'user:' + user_id}"

        if self._bot_account and user_id == self._bot_account:
            return unchanged

        plain_text = str(msg.get("processed_plain_text") or "").strip()
        now_ts = time.time()
        if session_id:
            self._session_to_group[session_id] = group_id
            if user_id:
                self._recent_senders[session_id] = user_id
                if not group_id:
                    self._session_to_private[session_id] = user_id

        components = _extract_components(msg)
        at_bot = False
        for comp in components:
            ctype = str(comp.get("type") or "")
            cdata = comp.get("data") if isinstance(comp.get("data"), dict) else comp
            if "At" in ctype or ctype == "at":
                tid = str(
                    cdata.get("target_user_id")
                    or cdata.get("qq")
                    or comp.get("target_user_id")
                    or comp.get("qq")
                    or ""
                ).strip()
                if self._bot_account and tid == self._bot_account:
                    at_bot = True

        aliases = self._all_aliases()
        mentioned_bot = at_bot or any(a.lower() in plain_text.lower() for a in aliases)

        sched = self._current_schedule()
        if (
            not sched.is_sleep
            and self.config.presence.poke_back_enabled
            and user_id
            and _POKE_TEXT_RE.search(plain_text)
            and (not group_id or mentioned_bot)
        ):
            last_poke = self._last_poke_back.get(session_id, 0.0)
            if (
                now_ts - last_poke >= float(self.config.presence.poke_back_cooldown_seconds)
                and self._rng.random() < float(self.config.presence.poke_back_probability)
            ):
                self._last_poke_back[session_id] = now_ts
                asyncio.create_task(self._send_poke_back(user_id=user_id, group_id=group_id))

        if self.config.link_preview.enabled and ("http://" in plain_text or "https://" in plain_text):
            urls = extract_previewable_urls(plain_text, skip_domains=self.config.link_preview.skip_domains)
            summaries: list[str] = []
            for url in urls:
                cached = self._link_cache.get(url, now_ts)
                if cached is None:
                    cached = await asyncio.to_thread(
                        fetch_url_summary_sync,
                        url,
                        timeout_seconds=self.config.link_preview.timeout_seconds,
                        max_chars=self.config.link_preview.max_summary_chars,
                    )
                    self._link_cache.set(url, cached, now_ts)
                if cached:
                    summaries.append(cached)
            if summaries:
                updated_msg = dict(msg)
                updated_msg["processed_plain_text"] = plain_text + "\n[网页链接摘要] " + " | ".join(summaries)
                updated_kwargs = dict(kwargs)
                updated_kwargs["message"] = updated_msg
                return {"action": "continue", "modified_kwargs": updated_kwargs}

        return unchanged

    async def _send_poke_back(self, *, user_id: str, group_id: str) -> None:
        try:
            await asyncio.sleep(self._rng.uniform(1.0, 2.8))
            params: dict[str, Any] = {"user_id": user_id}
            if group_id:
                params["group_id"] = group_id
            await self._bridge.call_action(self.ctx.api, "send_poke", params)
        except Exception:
            pass

    async def _ping_private_typing_status(self, user_id: str) -> None:
        if not user_id:
            return
        now = time.time()
        if now - self._last_typing_ping.get(user_id, 0.0) < 6.0:
            return
        self._last_typing_ping[user_id] = now
        uid_val: Any = int(user_id) if user_id.isdigit() else user_id
        await self._bridge.call_action(self.ctx.api, "set_input_status", {"user_id": uid_val, "event_type": 1})

    @HookHandler(
        "maisaka.planner.before_request",
        name="living_rhythm_planner_inject",
        description="私聊清醒期触发正在输入状态，并在深夜静默期提供最小兜底提示（常态零注入）",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        timeout_ms=1500,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_planner_before_request(self, **kwargs: Any) -> dict[str, Any]:
        unchanged = {"action": "continue", "modified_kwargs": kwargs}
        if not self._running or not self.config.plugin.enabled:
            return unchanged
        items = kwargs.get("items")
        if not isinstance(items, list):
            return unchanged

        session_id = str(kwargs.get("session_id") or "").strip()
        is_group, _, private_uid = self._resolve_session_context(session_id)
        latest_uid = _extract_last_user_from_items(items) or self._recent_senders.get(session_id, "")
        sched = self._current_schedule()

        if (
            not is_group
            and not sched.is_sleep
            and self.config.presence.enabled
            and self.config.presence.private_typing_status
        ):
            target_uid = private_uid or latest_uid
            if target_uid:
                asyncio.create_task(self._ping_private_typing_status(target_uid))

        if not (sched.is_sleep and sched.hint):
            return unchanged

        updated = dict(kwargs)
        updated["items"] = _inject_reminder(items, sched.hint)
        return {"action": "continue", "modified_kwargs": updated}

    @HookHandler(
        "send_service.before_send",
        name="living_rhythm_sleep_guard",
        description="深夜休眠时段拦截非系统通知消息出站，放行防撤回等系统提醒",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        timeout_ms=1500,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_before_send(self, **kwargs: Any) -> dict[str, Any]:
        unchanged = {"action": "continue", "modified_kwargs": kwargs}
        if (
            not self._running
            or not self.config.plugin.enabled
            or not self.config.schedule.enabled
            or not self.config.schedule.block_during_sleep
        ):
            return unchanged

        msg = kwargs.get("message")
        text = str(msg.get("processed_plain_text") or "") if isinstance(msg, dict) else ""
        if is_system_notification_text(text):
            return unchanged

        if self._current_schedule().is_sleep:
            return {"action": "abort"}
        return unchanged

    async def _sync_presence_if_changed(self, phase: str) -> None:
        if not self.config.presence.enabled or phase == self._last_synced_presence_phase:
            return
        target = resolve_presence_for_phase(phase)
        self._last_synced_presence_phase = phase
        ok, _ = await self._bridge.call_action(
            self.ctx.api,
            "set_online_status",
            {
                "status": target.status,
                "ext_status": target.ext_status,
                "battery_status": target.battery_status,
            },
        )
        if ok:
            self.ctx.logger.info("已同步 QQ 在线状态: phase=%s label=%s", target.phase, target.label)

    @staticmethod
    def _parse_frequency_value(raw: Any, default: float = 1.0) -> float:
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
        if isinstance(raw, dict):
            if raw.get("success") is False:
                return default
            for key in ("value", "adjust", "result"):
                val = raw.get(key)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    return float(val)
        return default

    async def _read_current_frequency(self, sid: str) -> float:
        getter = getattr(self.ctx.frequency, "get_adjust", None)
        if not callable(getter):
            return 1.0
        try:
            raw = await getter(sid)
            return self._parse_frequency_value(raw, default=1.0)
        except Exception:
            return 1.0

    async def _sync_sleep_frequency(self, is_sleep: bool) -> None:
        if not self.config.plugin.enabled or not self.config.schedule.enabled or not self.config.schedule.auto_adjust_frequency:
            if self._muted_sessions:
                restored = await self._restore_muted_sessions()
                self._sleep_freq_applied = False if restored else True
            else:
                self._sleep_freq_applied = False
            return

        if not is_sleep:
            if self._muted_sessions:
                restored = await self._restore_muted_sessions()
                self._sleep_freq_applied = False if restored else True
            else:
                self._sleep_freq_applied = False
            return

        if self._sleep_freq_applied is True:
            return

        try:
            raw_streams = await self.ctx.chat.get_group_streams(platform="qq")
            streams = (
                raw_streams.get("streams", [])
                if isinstance(raw_streams, dict)
                else (raw_streams if isinstance(raw_streams, list) else [])
            )
        except Exception:
            return
        if not isinstance(streams, list):
            return

        all_muted = True
        for st in streams:
            if not isinstance(st, dict):
                continue
            sid = str(st.get("session_id") or "").strip()
            if not sid:
                continue
            prev_val = self._muted_sessions.get(sid)
            if prev_val is None:
                prev_val = await self._read_current_frequency(sid)
            try:
                res = await self.ctx.frequency.set_adjust(sid, 0.0)
                if isinstance(res, dict) and res.get("success") is False:
                    all_muted = False
                    continue
                self._muted_sessions.setdefault(sid, prev_val)
            except Exception:
                all_muted = False
        if all_muted:
            self._sleep_freq_applied = True

    async def _restore_muted_sessions(self) -> bool:
        all_restored = True
        for sid, prev_val in list(self._muted_sessions.items()):
            try:
                res = await self.ctx.frequency.set_adjust(sid, prev_val)
                if isinstance(res, dict) and res.get("success") is False:
                    all_restored = False
                    continue
                self._muted_sessions.pop(sid, None)
            except Exception:
                all_restored = False
        return all_restored

    async def _run_scheduler_loop(self) -> None:
        while self._running:
            try:
                self._apply_config_to_engines()
                sched = self._current_schedule()
                await self._sync_sleep_frequency(sched.is_sleep)
                await self._sync_presence_if_changed(sched.phase)
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(60)


def create_plugin() -> LivingRhythmPlugin:
    return LivingRhythmPlugin()
