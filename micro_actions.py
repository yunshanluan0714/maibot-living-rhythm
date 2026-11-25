"""QQ presence/device sync, private typing indicator, and group micro-actions (solitaire/poll/redpacket)."""

from __future__ import annotations

from dataclasses import dataclass
import random
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
_REDPACKET_RE = re.compile(r"(?:\[QQ红包\]|\[红包\]|\[拼手气红包\]|收到一个红包|发了一个红包)", re.I)
_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*元")


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


@dataclass(frozen=True)
class RedpacketPlan:
    should_miss: bool
    delay_seconds: float
    reaction_text: str


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


def is_redpacket_message(text: str) -> bool:
    return bool(_REDPACKET_RE.search(str(text or "")))


def plan_redpacket_reaction(
    text: str,
    *,
    rng: random.Random | None = None,
    min_delay: float = 2.0,
    max_delay: float = 8.0,
    miss_prob: float = 0.22,
    opened_amount: float | None = None,
) -> RedpacketPlan:
    r = rng or random.Random()
    lo = max(1.5, float(min_delay))
    hi = max(lo + 0.5, float(max_delay))
    delay = round(r.uniform(lo, hi), 2)

    if r.random() < max(0.0, min(1.0, float(miss_prob))):
        return RedpacketPlan(should_miss=True, delay_seconds=delay, reaction_text="")

    amt = opened_amount
    if amt is None:
        m = _AMOUNT_RE.search(str(text or ""))
        if m:
            try:
                amt = float(m.group(1))
            except ValueError:
                amt = None

    if amt is not None:
        if amt >= 5.0:
            reply = r.choice(["谢谢老板！大气", "哇谢谢老板，吃顿好的", "老板发财！"])
        elif amt >= 0.5:
            reply = r.choice(["谢谢老板", "感谢老板的小红包", "美滋滋 谢谢老板"])
        else:
            reply = r.choice(["几分也是爱，谢谢老板", "好家伙这手气", "谢谢老板的巨款"])
    else:
        reply = r.choice(["谢谢老板", "手慢了差一点", "看看大家手气"])

    return RedpacketPlan(should_miss=False, delay_seconds=delay, reaction_text=reply)
