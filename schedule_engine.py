"""Virtual daily schedule state machine (zero daytime prompt bloat)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ScheduleState:
    phase: str
    label: str
    is_sleep: bool
    hint: str


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
        return ScheduleState(phase="WAKE_CATCHUP", label="刚睡醒", is_sleep=False, hint="")
    if 9 * 60 <= cur_min < 12 * 60:
        return ScheduleState(phase="MORNING_SLACK", label="上午日常", is_sleep=False, hint="")
    if 12 * 60 <= cur_min < 14 * 60:
        return ScheduleState(phase="LUNCH", label="午休干饭", is_sleep=False, hint="")
    if 14 * 60 <= cur_min < 18 * 60 + 30:
        return ScheduleState(phase="AFTERNOON_SLACK", label="下午日常", is_sleep=False, hint="")
    if 18 * 60 + 30 <= cur_min < 23 * 60 + 30:
        return ScheduleState(phase="EVENING_ACTIVE", label="晚间活跃", is_sleep=False, hint="")
    return ScheduleState(phase="LATE_DROWSY", label="睡前犯困", is_sleep=False, hint="")
