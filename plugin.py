"""Main plugin wiring for github.yunshanluan0714.living-rhythm."""

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
from .circle_radar import CircleRadarStore
from .config import LivingRhythmConfig
from .micro_actions import (
    SolitaireSignal,
    detect_group_poll,
    detect_group_solitaire,
    is_redpacket_message,
    plan_redpacket_reaction,
    resolve_presence_for_phase,
)
from .reality_anchor import RealityAnchorManager
from .schedule_engine import SleepCatchupStore, resolve_schedule_state, score_catchup_message

_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
_HEADER_RE = re.compile(r"^\s*<message\b([^>]*)>", re.S)
_POKE_TEXT_RE = re.compile(r"戳了戳|发起了戳一戳")


def _extract_last_user_from_items(items: list[dict[str, Any]]) -> tuple[str, str, str]:
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
        body = "".join([texts[0][match.end() :], *texts[1:]]).strip()
        return attrs.get("user", "").strip(), body, attrs.get("user_id", "").strip()
    return "", "", ""


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
    config_model = LivingRhythmConfig

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._rng = random.Random()
        self._bridge = DynamicAdapterBridge()
        self._sleep_store: SleepCatchupStore | None = None
        self._circle_radar: CircleRadarStore | None = None
        self._reality = RealityAnchorManager()
        self._loop_task: asyncio.Task[None] | None = None
        self._bot_account = ""
        self._bot_nickname = ""
        self._bot_aliases: list[str] = []
        self._pending_solitaire: dict[str, SolitaireSignal] = {}
        self._pending_poll: set[str] = set()
        self._last_group_activity: dict[str, float] = {}
        self._last_cold_trigger: dict[str, float] = {}
        self._cold_daily_count: dict[str, tuple[str, int]] = {}
        self._last_poke_back: dict[str, float] = {}
        self._last_private_proactive: dict[str, float] = {}
        self._last_typing_ping: dict[str, float] = {}
        self._recent_senders: dict[str, tuple[str, str]] = {}
        self._last_synced_presence_phase: str = ""
        self._sleep_freq_applied: bool | None = None
        self._muted_sessions: set[str] = set()

    async def on_load(self) -> None:
        data_dir = self.ctx.paths.data_dir
        self._sleep_store = SleepCatchupStore(data_dir)
        self._circle_radar = CircleRadarStore(
            data_dir,
            tags=self.config.circle_radar.interest_tags,
            refresh_hours=self.config.circle_radar.refresh_hours,
            max_items=self.config.circle_radar.max_items,
        )
        self._apply_config_to_engines()
        await self._refresh_bot_identity()
        self._running = True
        self._loop_task = asyncio.create_task(self._run_scheduler_loop(), name="living-rhythm-loop")
        self.ctx.logger.info("拟人作息、现实锚点与主动社交插件已加载")

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
        self._apply_config_to_engines()
        self._last_synced_presence_phase = ""
        await self._refresh_bot_identity()

    def _apply_config_to_engines(self) -> None:
        cfg = self.config
        self._bridge.set_override(cfg.presence.custom_action_api)
        self._reality.city = cfg.reality_anchor.city
        self._reality.province = cfg.reality_anchor.province
        self._reality.refresh_seconds = max(1800.0, float(cfg.reality_anchor.weather_refresh_hours) * 3600.0)
        if self._circle_radar is not None:
            self._circle_radar.update_config(
                tags=cfg.circle_radar.interest_tags,
                refresh_hours=cfg.circle_radar.refresh_hours,
                max_items=cfg.circle_radar.max_items,
            )

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

    @HookHandler(
        "chat.receive.after_process",
        name="living_rhythm_receive_after_process",
        description="记录休眠期高价值消息、识别接龙/投票/红包延迟互动与清醒期回戳",
        mode=HookMode.OBSERVE,
        order=HookOrder.NORMAL,
        timeout_ms=3000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_receive_after_process(self, **kwargs: Any) -> None:
        if not self._running or not self.config.plugin.enabled:
            return
        msg = kwargs.get("message")
        if not isinstance(msg, dict):
            return

        msg_info = msg.get("message_info") if isinstance(msg.get("message_info"), dict) else {}
        user_info = msg_info.get("user_info") if isinstance(msg_info.get("user_info"), dict) else {}
        group_info = msg_info.get("group_info") if isinstance(msg_info.get("group_info"), dict) else None
        user_id = str(user_info.get("user_id") or "").strip()
        sender_name = str(user_info.get("user_cardname") or user_info.get("user_nickname") or user_id).strip()
        group_id = str(group_info.get("group_id") or "").strip() if group_info else ""
        session_id = str(msg.get("session_id") or "").strip()
        msg_id = str(msg.get("message_id") or "").strip()
        platform = str(msg_info.get("platform") or "qq").strip()
        if not session_id and platform:
            session_id = f"{platform}:{'group:' + group_id if group_id else 'user:' + user_id}"

        if self._bot_account and user_id == self._bot_account:
            return

        plain_text = str(msg.get("processed_plain_text") or "").strip()
        now_ts = time.time()
        if session_id and user_id:
            self._recent_senders[session_id] = (user_id, sender_name)
        if group_id:
            self._last_group_activity[group_id] = now_ts

        raw_msg = msg.get("raw_message") if isinstance(msg.get("raw_message"), dict) else {}
        components = raw_msg.get("components") if isinstance(raw_msg.get("components"), list) else []
        at_bot = False
        for comp in components:
            if not isinstance(comp, dict):
                continue
            ctype = str(comp.get("type") or "")
            if "At" in ctype or ctype == "at":
                tid = str(comp.get("target_user_id") or comp.get("qq") or "").strip()
                if self._bot_account and tid == self._bot_account:
                    at_bot = True

        aliases = self._all_aliases()
        mentioned_bot = at_bot or any(a.lower() in plain_text.lower() for a in aliases)

        if group_id and self.config.micro_actions.enabled:
            if self.config.micro_actions.solitaire_enabled:
                sol = detect_group_solitaire(plain_text)
                if sol is not None:
                    self._pending_solitaire[session_id] = sol
            if self.config.micro_actions.poll_enabled and detect_group_poll(plain_text):
                self._pending_poll.add(session_id)

        sched = self._current_schedule()
        if sched.is_sleep and self.config.schedule.wake_catchup_enabled and self._sleep_store is not None:
            score = score_catchup_message(
                plain_text,
                is_private=not bool(group_id),
                is_at_bot=at_bot,
                bot_aliases=aliases,
            )
            if score >= 2.0:
                self._sleep_store.record_sleep_message(
                    session_id=session_id,
                    group_id=group_id,
                    user_id=user_id,
                    sender_name=sender_name,
                    message_id=msg_id,
                    text=plain_text,
                    score=score,
                    created_at=now_ts,
                )

        if (
            not sched.is_sleep
            and group_id
            and self.config.micro_actions.enabled
            and self.config.micro_actions.redpacket_enabled
            and is_redpacket_message(plain_text)
        ):
            asyncio.create_task(self._handle_redpacket_delayed(session_id=session_id, group_id=group_id, text=plain_text))

        if (
            not sched.is_sleep
            and self.config.proactive.enabled
            and self.config.proactive.poke_back_enabled
            and user_id
            and _POKE_TEXT_RE.search(plain_text)
            and (not group_id or mentioned_bot)
        ):
            last_poke = self._last_poke_back.get(session_id, 0.0)
            if (
                now_ts - last_poke >= float(self.config.proactive.poke_back_cooldown_seconds)
                and self._rng.random() < float(self.config.proactive.poke_back_probability)
            ):
                self._last_poke_back[session_id] = now_ts
                asyncio.create_task(self._send_poke_back(user_id=user_id, group_id=group_id))

    async def _handle_redpacket_delayed(self, *, session_id: str, group_id: str, text: str) -> None:
        try:
            plan = plan_redpacket_reaction(
                text,
                rng=self._rng,
                min_delay=self.config.micro_actions.redpacket_min_delay_sec,
                max_delay=self.config.micro_actions.redpacket_max_delay_sec,
                miss_prob=self.config.micro_actions.redpacket_miss_probability,
            )
            await asyncio.sleep(plan.delay_seconds)
            if not self._running or self._current_schedule().is_sleep or plan.should_miss:
                return

            opened_amount: float | None = None
            custom_action = str(self.config.micro_actions.redpacket_open_action or "").strip()
            if custom_action:
                ok, res = await self._bridge.call_action(self.ctx.api, custom_action, {"group_id": group_id})
                if ok and isinstance(res, dict):
                    raw_amt = res.get("amount") or res.get("money")
                    try:
                        if raw_amt is not None:
                            opened_amount = float(raw_amt)
                    except (TypeError, ValueError):
                        opened_amount = None
                if opened_amount is not None:
                    plan = plan_redpacket_reaction(text, rng=self._rng, miss_prob=0.0, opened_amount=opened_amount)

            if plan.reaction_text:
                await self.ctx.send.text(plan.reaction_text, session_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

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
        description="注入作息状态、接龙/投票提示、现实天气节假日锚点与圈层热榜见闻",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
        timeout_ms=2000,
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
        is_group = ":group:" in session_id
        private_uid = session_id.split(":user:")[-1] if (not is_group and ":user:" in session_id) else ""
        _, latest_text, latest_uid = _extract_last_user_from_items(items)
        if not latest_uid and session_id in self._recent_senders:
            latest_uid, _ = self._recent_senders[session_id]

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

        hints: list[str] = []
        if sched.hint:
            hints.append(sched.hint)

        if is_group and self.config.micro_actions.enabled:
            sol = self._pending_solitaire.pop(session_id, None)
            if sol is not None:
                hints.append(f"[群接龙] 群友发起了「{sol.topic}」接龙，当前到第 {sol.next_index - 1} 项，若话题相关可按格式顺手接第 {sol.next_index} 条。")
            if session_id in self._pending_poll:
                self._pending_poll.discard(session_id)
                hints.append("[群投票] 群里发起了投票，如果感兴趣可以随口表态或评论几句，不感兴趣就略过。")

        if self.config.reality_anchor.enabled:
            cal_trigger = any(k in latest_text for k in ("放假", "节", "调休", "上班", "早八", "考试", "期末", "周五", "周一", "双11", "618"))
            ra_hint = self._reality.build_hint(
                latest_text=latest_text,
                adjusted_workdays=self.config.reality_anchor.adjusted_workdays,
                extra_holidays=self.config.reality_anchor.extra_holidays,
                include_calendar=cal_trigger,
            )
            if ra_hint:
                hints.append(ra_hint)

        if self.config.circle_radar.enabled and self._circle_radar is not None and latest_text:
            radar_hint = self._circle_radar.match_hint(latest_text)
            if radar_hint:
                hints.append(radar_hint)

        if not hints:
            return unchanged

        updated = dict(kwargs)
        updated["items"] = _inject_reminder(items, "\n".join(hints))
        return {"action": "continue", "modified_kwargs": updated}

    @HookHandler(
        "send_service.before_send",
        name="living_rhythm_sleep_guard",
        description="深夜休眠时段硬拦截非系统回复出站，确保作息真实离线",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        timeout_ms=1500,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_before_send(self, **kwargs: Any) -> dict[str, Any]:
        unchanged = {"action": "continue", "modified_kwargs": kwargs}
        if not self._running or not self.config.plugin.enabled or not self.config.schedule.enabled:
            return unchanged
        if self._current_schedule().is_sleep:
            return {"action": "abort"}
        return unchanged

    async def _sync_presence_if_changed(self, phase: str) -> None:
        if not self.config.presence.enabled or phase == self._last_synced_presence_phase:
            return
        target = resolve_presence_for_phase(phase)
        self._last_synced_presence_phase = phase
        await self._bridge.call_action(
            self.ctx.api,
            "set_online_status",
            {
                "status": target.status,
                "ext_status": target.ext_status,
                "battery_status": target.battery_status,
            },
        )
        if self.config.presence.sync_device_model:
            model_name = (
                self.config.presence.day_device_name
                if target.device_kind == "day"
                else self.config.presence.night_device_name
            )
            if model_name.strip():
                await self._bridge.call_action(self.ctx.api, "_set_model_show", {"model_show": model_name.strip()})

    async def _sync_sleep_frequency(self, is_sleep: bool) -> None:
        if not self.config.schedule.auto_adjust_frequency:
            return
        if self._sleep_freq_applied is is_sleep:
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

        if is_sleep:
            for st in streams:
                if not isinstance(st, dict):
                    continue
                sid = str(st.get("session_id") or "").strip()
                if not sid:
                    continue
                try:
                    await self.ctx.frequency.set_adjust(sid, 0.0)
                    self._muted_sessions.add(sid)
                except Exception:
                    pass
        else:
            await self._restore_muted_sessions()
        self._sleep_freq_applied = is_sleep

    async def _restore_muted_sessions(self) -> None:
        for sid in list(self._muted_sessions):
            try:
                await self.ctx.frequency.set_adjust(sid, 1.0)
            except Exception:
                pass
            self._muted_sessions.discard(sid)

    async def _run_scheduler_loop(self) -> None:
        while self._running:
            try:
                self._apply_config_to_engines()
                sched = self._current_schedule()
                await self._sync_sleep_frequency(sched.is_sleep)
                await self._sync_presence_if_changed(sched.phase)

                if self.config.reality_anchor.enabled and self._reality.needs_refresh():
                    await asyncio.to_thread(self._reality.refresh_sync)

                if self.config.circle_radar.enabled and self._circle_radar is not None and self._circle_radar.needs_refresh():
                    await asyncio.to_thread(self._circle_radar.refresh_sync)

                if not sched.is_sleep:
                    if self.config.schedule.wake_catchup_enabled and self._sleep_store is not None:
                        await self._process_wake_catchup()

                    if self.config.proactive.enabled and sched.phase not in ("LATE_DROWSY",):
                        await self._process_cold_chat_and_private()

                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(60)

    async def _process_wake_catchup(self) -> None:
        if self._sleep_store is None or self._sleep_store.pending_count() <= 0:
            return
        targets = self._sleep_store.pop_catchup_targets(
            max_per_group=self.config.schedule.wake_catchup_max_per_group,
        )
        for item in targets:
            try:
                instruction = (
                    f"[睡醒爬楼] 你昨晚休息时 {item.sender_name or '群友'} 发了这条消息：「{item.text}」。"
                    "现在刚醒，用简短自然、像真人的口吻补回一两句；如果已经过去太久没必要提就 stop。"
                )
                await self.ctx.maisaka.proactive.trigger(
                    stream_id=item.session_id,
                    intent=instruction,
                    reason="wake_catchup",
                )
                await asyncio.sleep(self._rng.uniform(4.0, 9.0))
            except Exception:
                pass

    async def _process_cold_chat_and_private(self) -> None:
        now_ts = time.time()
        today_key = datetime.now().strftime("%Y-%m-%d")
        idle_sec = float(self.config.proactive.cold_chat_idle_minutes) * 60.0
        cooldown_sec = float(self.config.proactive.cold_chat_cooldown_hours) * 3600.0

        if self.config.proactive.cold_chat_daily_limit > 0:
            try:
                raw_streams = await self.ctx.chat.get_group_streams(platform="qq")
                streams = (
                    raw_streams.get("streams", [])
                    if isinstance(raw_streams, dict)
                    else (raw_streams if isinstance(raw_streams, list) else [])
                )
            except Exception:
                streams = []
            if isinstance(streams, list):
                for st in streams:
                    if not isinstance(st, dict):
                        continue
                    gid = str(st.get("group_id") or "").strip()
                    sid = str(st.get("session_id") or "").strip()
                    if not gid or not sid:
                        continue
                    last_act = self._last_group_activity.get(gid, 0.0)
                    if last_act <= 0.0 or now_ts - last_act < idle_sec:
                        continue
                    if now_ts - self._last_cold_trigger.get(gid, 0.0) < cooldown_sec:
                        continue
                    day_str, cnt = self._cold_daily_count.get(gid, (today_key, 0))
                    if day_str != today_key:
                        cnt = 0
                    if cnt >= self.config.proactive.cold_chat_daily_limit:
                        continue
                    if self._rng.random() > 0.25:
                        continue
                    self._last_cold_trigger[gid] = now_ts
                    self._cold_daily_count[gid] = (today_key, cnt + 1)

                    radar_topic = self._circle_radar.pick_icebreak_topic(now_ts=now_ts) if self._circle_radar else None
                    extra_topic = ""
                    if radar_topic is not None:
                        extra_topic = f"也可以顺口提一句你刚在{radar_topic.source}刷到的「{radar_topic.title}」。"
                    elif self._reality.weather is not None and self._reality.weather.is_notable():
                        extra_topic = f"或者顺嘴吐槽一句{self._reality.weather.city}这边{self._reality.weather.condition}（{self._reality.weather.temp_c}°C）。"

                    try:
                        await self.ctx.maisaka.proactive.trigger(
                            stream_id=sid,
                            intent=(
                                f"[冷群破冰] 这个群安静好久了。自然随口冒个泡聊点日常，{extra_topic}"
                                "简短一两句即可，不要硬找尴尬话题。"
                            ),
                            reason="cold_chat_icebreak",
                        )
                    except Exception:
                        pass
                    break

        if self.config.proactive.private_proactive_enabled and self.config.proactive.private_whitelist_users:
            p_cooldown = float(self.config.proactive.private_cooldown_hours) * 3600.0
            for raw_uid in self.config.proactive.private_whitelist_users:
                uid = str(raw_uid or "").strip()
                if not uid or uid == self._bot_account:
                    continue
                if now_ts - self._last_private_proactive.get(uid, 0.0) < p_cooldown:
                    continue
                if self._rng.random() > 0.12:
                    continue
                self._last_private_proactive[uid] = now_ts
                try:
                    opened = await self.ctx.chat.open_session(platform="qq", chat_type="private", user_id=uid)
                    sid = ""
                    if isinstance(opened, dict):
                        nested = opened.get("stream")
                        if isinstance(nested, dict):
                            sid = str(nested.get("session_id") or "")
                        if not sid:
                            sid = str(opened.get("session_id") or "")
                    if sid:
                        await self.ctx.maisaka.proactive.trigger(
                            stream_id=sid,
                            intent="[熟人私聊] 随口找熟人轻聊一两句日常近况或刚看到的趣事，不要写长篇问候。",
                            reason="proactive_private_ping",
                        )
                except Exception:
                    pass
                break


def create_plugin() -> LivingRhythmPlugin:
    return LivingRhythmPlugin()
