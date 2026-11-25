"""QQ online presence presets for schedule phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Standard NapCat / SnowLuma / OneBot v11 online status presets
_PHASE_STATUS_MAP: dict[str, dict[str, Any]] = {
    "SLEEP": {"label": "睡觉中", "status": 10, "ext_status": 1016, "battery_status": 0},
    "WAKE_CATCHUP": {"label": "在线", "status": 10, "ext_status": 0, "battery_status": 0},
    "MORNING_SLACK": {"label": "学习中", "status": 10, "ext_status": 1018, "battery_status": 0},
    "LUNCH": {"label": "干饭中", "status": 10, "ext_status": 1019, "battery_status": 0},
    "AFTERNOON_SLACK": {"label": "听歌中", "status": 10, "ext_status": 1028, "battery_status": 0},
    "EVENING_ACTIVE": {"label": "听歌中", "status": 10, "ext_status": 1028, "battery_status": 0},
    "LATE_DROWSY": {"label": "离开", "status": 30, "ext_status": 0, "battery_status": 0},
}


@dataclass(frozen=True)
class PresenceTarget:
    phase: str
    label: str
    status: int
    ext_status: int
    battery_status: int


def resolve_presence_for_phase(phase: str) -> PresenceTarget:
    cfg = _PHASE_STATUS_MAP.get(phase) or _PHASE_STATUS_MAP["EVENING_ACTIVE"]
    return PresenceTarget(
        phase=phase,
        label=str(cfg["label"]),
        status=int(cfg["status"]),
        ext_status=int(cfg["ext_status"]),
        battery_status=int(cfg["battery_status"]),
    )
