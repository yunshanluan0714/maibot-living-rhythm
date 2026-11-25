"""QQ presence/device sync, private typing indicator, and group micro-actions (solitaire/poll)."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

# Standard NapCat / OneBot v11 / NTQQ online status presets
_PHASE_STATUS_MAP: dict[str, dict[str, Any]] = {
    "SLEEP": {"label": "睡觉中", "status": 10, "ext_status": 1016, "battery_status": 0, "device": "night"},
    "WAKE_CATCHUP": {"label": "在线", "status": 10, "ext_status": 0, "battery_status": 0, "device": "night"},
    "MORNING_SLACK": {"label": "学习中", "status": 10, "ext_status": 1018, "battery_status": 0, "device": "day"},
    "LUNCH": {"label": "干饭中", "status": 10, "ext_status": 1019, "battery_status": 0, "device": "night"},
    "AFTERNOON_SLACK": {"label": "听歌中", "status": 10, "ext_status": 1028, "battery_status": 0, "device": "day"},
    "EVENING_ACTIVE": {"label": "听歌中", "status": 10, "ext_status": 1028, "battery_status": 0, "device": "night"},
    "LATE_DROWSY": {"label": "离开", "status": 30, "ext_status": 0, "battery_status": 0, "device": "night"},
}

_SOLITAIRE_NUM_RE = re.compile(r"(?:^|\n)\s*(\d+)\.\s+([^\n]+)")
_POLL_RE = re.compile(r"(?:\[群投票\]|\[投票\]|发起了群投票|邀请你参与投票)", re.I)


@dataclass(frozen=True)
class PresenceTarget:
    phase: str
    label: str
    status: int
    ext_status: int
    battery_status: int
    device_kind: str


@dataclass(frozen=True)
class SolitaireSignal:
    next_index: int
    topic: str
    last_item: str


def resolve_presence_for_phase(phase: str) -> PresenceTarget:
    cfg = _PHASE_STATUS_MAP.get(phase) or _PHASE_STATUS_MAP["EVENING_ACTIVE"]
    return PresenceTarget(
        phase=phase,
        label=str(cfg["label"]),
        status=int(cfg["status"]),
        ext_status=int(cfg["ext_status"]),
        battery_status=int(cfg["battery_status"]),
        device_kind=str(cfg["device"]),
    )


def detect_group_solitaire(text: str) -> SolitaireSignal | None:
    clean = str(text or "").strip()
    if "#接龙" not in clean:
        return None
    nums = _SOLITAIRE_NUM_RE.findall(clean)
    if not nums:
        return None
    max_num = max(int(n) for n, _ in nums)
    last_item = nums[-1][1].strip()[:30]
    first_line = clean.splitlines()[0].replace("#接龙", "").strip()[:40]
    return SolitaireSignal(next_index=max_num + 1, topic=first_line or "群接龙", last_item=last_item)


def detect_group_poll(text: str) -> bool:
    return bool(_POLL_RE.search(str(text or "")))
