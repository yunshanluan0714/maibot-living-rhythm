"""Reality anchors: configurable city live weather, Chinese holidays/adjusted workdays, exam/promo dates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import time
from typing import Any, Iterable
import urllib.parse
import urllib.request

_CITY_PRESETS: dict[str, tuple[str, str]] = {
    "上海": ("上海市", "Shanghai"),
    "北京": ("北京市", "Beijing"),
    "杭州": ("浙江省", "Hangzhou"),
    "广州": ("广东省", "Guangzhou"),
    "深圳": ("广东省", "Shenzhen"),
    "成都": ("四川省", "Chengdu"),
    "武汉": ("湖北省", "Wuhan"),
    "南京": ("江苏省", "Nanjing"),
    "西安": ("陕西省", "Xian"),
    "苏州": ("江苏省", "Suzhou"),
    "重庆": ("重庆市", "Chongqing"),
    "长沙": ("湖南省", "Changsha"),
    "厦门": ("福建省", "Xiamen"),
    "青岛": ("山东省", "Qingdao"),
    "郑州": ("河南省", "Zhengzhou"),
    "合肥": ("安徽省", "Hefei"),
}

_FIXED_HOLIDAYS: dict[tuple[int, int], str] = {
    (1, 1): "元旦假期，大家多半在休假或跨年躺平",
    (5, 1): "五一劳动节假期，出去玩的人多容易堵车",
    (5, 2): "五一假期中",
    (5, 3): "五一假期中",
    (10, 1): "国庆长假第一天，景区和高速都在堵",
    (10, 2): "国庆长假中",
    (10, 3): "国庆长假中",
    (10, 7): "国庆假期最后一天，大家都在哀嚎明天上班",
}

_SHOPPING_EVENTS: dict[tuple[int, int], str] = {
    (6, 18): "今天是618大促，群里如果聊买东西/凑单可以顺嘴吐槽",
    (11, 11): "今天是双11，买买买/尾款人话题正热",
}


@dataclass(frozen=True)
class WeatherSnapshot:
    city: str
    condition: str
    temp_c: str
    fetched_at: float

    def is_notable(self) -> bool:
        cond = self.condition
        if any(k in cond for k in ("雨", "雪", "雷", "暴", "雾", "霾", "沙尘", "rain", "snow", "storm", "thunder")):
            return True
        try:
            t = float(self.temp_c)
            return t >= 34.0 or t <= 5.0
        except Exception:
            return False

    def hint_text(self) -> str:
        if not self.is_notable():
            return ""
        return f"[现实锚点-天气] {self.city}当前{self.condition}（{self.temp_c}°C），聊到出门/外卖/穿衣时可顺嘴提一句，别硬插。"


def _resolve_tz(tz_name: str) -> Any:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception:
        return timezone(timedelta(hours=8))


def fetch_city_weather_sync(city: str = "上海", province: str = "", *, timeout_seconds: float = 5.0) -> WeatherSnapshot | None:
    clean_city = str(city or "").strip()
    if not clean_city:
        return None
    preset = _CITY_PRESETS.get(clean_city)
    clean_province = str(province or "").strip() or (preset[0] if preset else "")
    city_en = preset[1] if preset else clean_city

    if clean_province:
        city_q = clean_city if clean_city.endswith("市") else clean_city + "市"
        url = (
            "https://wis.qq.com/weather/common?source=pc&weather_type=observe&"
            + urllib.parse.urlencode({"province": clean_province, "city": city_q})
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=float(timeout_seconds)) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            obs = ((data.get("data") or {}).get("observe") or {}) if isinstance(data, dict) else {}
            if obs.get("weather") and obs.get("degree") is not None:
                return WeatherSnapshot(
                    city=clean_city,
                    condition=str(obs["weather"]),
                    temp_c=str(obs["degree"]),
                    fetched_at=time.time(),
                )
        except Exception:
            pass

    try:
        wttr_url = f"https://wttr.in/{urllib.parse.quote(city_en)}?format=j1"
        req = urllib.request.Request(wttr_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=float(timeout_seconds)) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        cur = (data.get("current_condition") or [{}])[0]
        desc = ((cur.get("weatherDesc") or [{}])[0]).get("value") or ""
        temp = cur.get("temp_C")
        if desc and temp is not None:
            return WeatherSnapshot(
                city=clean_city,
                condition=str(desc),
                temp_c=str(temp),
                fetched_at=time.time(),
            )
    except Exception:
        pass
    return None


def resolve_calendar_anchor(
    today: date | None = None,
    *,
    adjusted_workdays: Iterable[str] = (),
    extra_holidays: Iterable[str] = (),
) -> str:
    d = today or datetime.now(_resolve_tz("Asia/Shanghai")).date()
    iso = d.isoformat()

    if iso in {str(x).strip() for x in adjusted_workdays if str(x).strip()}:
        return "[现实锚点-日历] 今天是周末调休补班日，打工人/学生党容易怨气重，聊到上班上学可顺嘴吐槽调休。"
    if iso in {str(x).strip() for x in extra_holidays if str(x).strip()}:
        return "[现实锚点-日历] 今天是法定节假日，氛围轻松休闲。"

    md = (d.month, d.day)
    if md in _FIXED_HOLIDAYS:
        return f"[现实锚点-日历] {_FIXED_HOLIDAYS[md]}。"
    if md in _SHOPPING_EVENTS:
        return f"[现实锚点-日历] {_SHOPPING_EVENTS[md]}。"

    if d.month == 6 and d.day in (7, 8, 9):
        return "[现实锚点-日历] 这几天正值高考，聊到考试可顺嘴提一句。"
    if d.month == 12 and 20 <= d.day <= 24:
        return "[现实锚点-日历] 考研季临近/进行中，不少学生在最后冲刺。"
    if (d.month == 1 and 5 <= d.day <= 20) or (d.month == 6 and 15 <= d.day <= 30):
        return "[现实锚点-日历] 期末周前后，学生党大多在赶论文或复习。"

    if d.weekday() == 4:
        return "[现实锚点-日历] 今天周五，下班/放学前心情普遍不错。"
    if d.weekday() == 0:
        return "[现实锚点-日历] 今天周一，早八/上班自带一点疲惫摸鱼感。"
    return ""


class RealityAnchorManager:
    """Caches live weather and serves contextual reality anchors."""

    def __init__(self, *, city: str = "上海", province: str = "上海市", refresh_hours: float = 2.0) -> None:
        self.city = city
        self.province = province
        self.refresh_seconds = max(1800.0, float(refresh_hours) * 3600.0)
        self.weather: WeatherSnapshot | None = None
        self._last_attempt: float = 0.0

    def needs_refresh(self, now_ts: float | None = None) -> bool:
        if not str(self.city or "").strip():
            return False
        now = float(now_ts if now_ts is not None else time.time())
        return (now - self._last_attempt) >= self.refresh_seconds

    def refresh_sync(self, *, timeout_seconds: float = 5.0) -> WeatherSnapshot | None:
        self._last_attempt = time.time()
        snap = fetch_city_weather_sync(self.city, self.province, timeout_seconds=timeout_seconds)
        if snap is not None:
            self.weather = snap
        return snap

    def build_hint(
        self,
        *,
        latest_text: str = "",
        today: date | None = None,
        adjusted_workdays: Iterable[str] = (),
        extra_holidays: Iterable[str] = (),
        include_calendar: bool = False,
    ) -> str:
        parts: list[str] = []
        if self.weather is not None and self.weather.is_notable():
            w_hint = self.weather.hint_text()
            if w_hint and any(k in latest_text for k in ("天气", "下雨", "冷", "热", "出门", "外卖", "温度", "风", "伞")):
                parts.append(w_hint)
        if include_calendar:
            c_hint = resolve_calendar_anchor(
                today,
                adjusted_workdays=adjusted_workdays,
                extra_holidays=extra_holidays,
            )
            if c_hint:
                parts.append(c_hint)
        return "\n".join(parts)
