"""Virtual daily schedule state machine and sleep-period catchup queue."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ScheduleState:
    phase: str
    label: str
    is_sleep: bool
    hint: str


@dataclass
class CatchupCandidate:
    session_id: str
    group_id: str
    user_id: str
    sender_name: str
    message_id: str
    text: str
    score: float
    created_at: float


def parse_hhmm(value: str, fallback: tuple[int, int]) -> int:
    text = str(value or "").strip()
    try:
        parts = text.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
    except Exception:
        pass
    return fallback[0] * 60 + fallback[1]


def in_minute_window(minute_of_day: int, start_min: int, end_min: int) -> bool:
    if start_min == end_min:
        return False
    if start_min < end_min:
        return start_min <= minute_of_day < end_min
    return minute_of_day >= start_min or minute_of_day < end_min


def _resolve_tz(tz_name: str) -> Any:
    name = str(tz_name or "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except Exception:
        if name in ("Asia/Shanghai", "Asia/Chongqing", "Asia/Harbin", "PRC", "UTC+8"):
            return timezone(timedelta(hours=8))
        return datetime.now().astimezone().tzinfo or timezone(timedelta(hours=8))


def current_local_dt(now_ts: float | None = None, timezone_name: str = "Asia/Shanghai") -> datetime:
    tz = _resolve_tz(timezone_name)
    if now_ts is None:
        return datetime.now(tz)
    return datetime.fromtimestamp(float(now_ts), tz=tz)


def resolve_schedule_state(
    *,
    now_ts: float | None = None,
    timezone_name: str = "Asia/Shanghai",
    sleep_start: str = "01:00",
    sleep_end: str = "07:30",
    enabled: bool = True,
) -> ScheduleState:
    if not enabled:
        return ScheduleState(phase="ALWAYS_ON", label="正常在线", is_sleep=False, hint="")

    dt = current_local_dt(now_ts, timezone_name)
    cur_min = dt.hour * 60 + dt.minute
    s_min = parse_hhmm(sleep_start, (1, 0))
    e_min = parse_hhmm(sleep_end, (7, 30))

    if in_minute_window(cur_min, s_min, e_min):
        return ScheduleState(
            phase="SLEEP",
            label="深夜休眠",
            is_sleep=True,
            hint="当前处于深夜睡眠时段，保持离线静默。",
        )

    wake_end = (e_min + 90) % 1440
    if in_minute_window(cur_min, e_min, wake_end):
        return ScheduleState(
            phase="WAKE_CATCHUP",
            label="刚睡醒爬楼",
            is_sleep=False,
            hint="[作息状态] 你刚睡醒不久，说话可以稍微懒散随和；看到休眠期留下的重要消息可以顺口补回一两句。",
        )
    if 9 * 60 <= cur_min < 12 * 60:
        return ScheduleState(
            phase="MORNING_SLACK",
            label="上午日常",
            is_sleep=False,
            hint="[作息状态] 上午在线时段，语气轻松简短，像顺手刷群聊两句。",
        )
    if 12 * 60 <= cur_min < 14 * 60:
        return ScheduleState(
            phase="LUNCH",
            label="午休干饭",
            is_sleep=False,
            hint="[作息状态] 中午午休/干饭时段，反应偏随性轻松。",
        )
    if 14 * 60 <= cur_min < 18 * 60 + 30:
        return ScheduleState(
            phase="AFTERNOON_SLACK",
            label="下午日常",
            is_sleep=False,
            hint="[作息状态] 下午日常在线摸鱼时段，保持自然随和语感。",
        )
    if 18 * 60 + 30 <= cur_min < 23 * 60 + 30:
        return ScheduleState(
            phase="EVENING_ACTIVE",
            label="晚间活跃",
            is_sleep=False,
            hint="",
        )
    return ScheduleState(
        phase="LATE_DROWSY",
        label="睡前犯困",
        is_sleep=False,
        hint="[作息状态] 快到休息时间了，语气比白天更精简随意，不想聊太费脑子的长话题。",
    )


def score_catchup_message(
    text: str,
    *,
    is_private: bool,
    is_at_bot: bool,
    bot_aliases: Iterable[str] = (),
) -> float:
    clean = str(text or "").strip()
    if not clean:
        return 0.0
    score = 0.0
    if is_private:
        score += 3.0
    if is_at_bot:
        score += 4.0
    lower = clean.lower()
    if any(alias.strip().lower() in lower for alias in bot_aliases if alias and alias.strip()):
        score += 2.5
    if "?" in clean or "？" in clean:
        score += 1.2
    if len(clean) >= 18:
        score += 0.8
    if clean.startswith("[") and clean.endswith("]") and len(clean) <= 16:
        score -= 1.0
    return score


class SleepCatchupStore:
    """Persists high-value unread messages collected during sleep across restarts."""

    def __init__(self, data_dir: Path | str) -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._catchup_path = self._dir / "sleep_catchup.json"
        self._candidates: dict[str, list[CatchupCandidate]] = {}
        self._load_catchup()

    def record_sleep_message(
        self,
        *,
        session_id: str,
        group_id: str,
        user_id: str,
        sender_name: str,
        message_id: str,
        text: str,
        score: float,
        created_at: float,
    ) -> None:
        if score < 2.0 or not session_id:
            return
        bucket = self._candidates.setdefault(session_id, [])
        if any(item.message_id == message_id and message_id for item in bucket):
            return
        bucket.append(
            CatchupCandidate(
                session_id=session_id,
                group_id=group_id,
                user_id=user_id,
                sender_name=sender_name,
                message_id=message_id,
                text=text[:180],
                score=round(score, 2),
                created_at=float(created_at),
            )
        )
        bucket.sort(key=lambda item: (item.score, item.created_at), reverse=True)
        if len(bucket) > 12:
            del bucket[12:]
        self._save_catchup()

    def pop_catchup_targets(
        self,
        *,
        max_per_group: int = 2,
        max_age_seconds: float = 12 * 3600,
        now_ts: float | None = None,
    ) -> list[CatchupCandidate]:
        if max_per_group <= 0 or not self._candidates:
            return []
        now = float(now_ts if now_ts is not None else datetime.now().timestamp())
        picked: list[CatchupCandidate] = []
        for session_id, items in list(self._candidates.items()):
            fresh = [c for c in items if now - c.created_at <= max_age_seconds]
            fresh.sort(key=lambda c: (c.score, c.created_at), reverse=True)
            picked.extend(fresh[:max_per_group])
            del self._candidates[session_id]
        self._save_catchup()
        return picked

    def pending_count(self) -> int:
        return sum(len(v) for v in self._candidates.values())

    def _load_catchup(self) -> None:
        if not self._catchup_path.is_file():
            return
        try:
            raw = json.loads(self._catchup_path.read_text("utf-8"))
        except Exception:
            return
        if not isinstance(raw, dict):
            return
        for sid, items in raw.items():
            if not isinstance(items, list):
                continue
            parsed: list[CatchupCandidate] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    parsed.append(CatchupCandidate(**item))
                except TypeError:
                    continue
            if parsed:
                self._candidates[str(sid)] = parsed

    def _save_catchup(self) -> None:
        data = {sid: [asdict(c) for c in items] for sid, items in self._candidates.items() if items}
        tmp = self._catchup_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._catchup_path)
