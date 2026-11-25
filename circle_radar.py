"""Low-frequency circle radar fetching hot topics matching configurable interest tags."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from typing import Iterable
import urllib.request


@dataclass
class CircleTopic:
    title: str
    source: str
    tag: str
    fetched_at: float


def fetch_circle_topics_sync(
    tags: Iterable[str],
    *,
    max_items: int = 8,
    timeout_seconds: float = 5.0,
) -> list[CircleTopic]:
    clean_tags = [str(t or "").strip() for t in tags if str(t or "").strip()]
    if not clean_tags:
        return []
    lower_tags = [(t, t.lower()) for t in clean_tags]
    now = time.time()
    collected: list[CircleTopic] = []
    seen_titles: set[str] = set()

    def _match_tag(text: str) -> str:
        low = text.lower()
        for orig, low_t in lower_tags:
            if low_t in low:
                return orig
        return ""

    try:
        req = urllib.request.Request(
            "https://s.search.bilibili.com/main/hotword",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=float(timeout_seconds)) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        for item in data.get("list") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("show_name") or item.get("keyword") or "").strip()
            if not title or title in seen_titles:
                continue
            tag = _match_tag(title)
            if tag:
                seen_titles.add(title)
                collected.append(CircleTopic(title=title, source="B站热搜", tag=tag, fetched_at=now))
                if len(collected) >= max_items:
                    return collected
    except Exception:
        pass

    for rid, src_label in ((4, "B站游戏区"), (1, "B站动画区"), (188, "B站科技区")):
        if len(collected) >= max_items:
            break
        try:
            url = f"https://api.bilibili.com/x/web-interface/ranking/v2?rid={rid}&type=all"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=float(timeout_seconds)) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            items = ((data.get("data") or {}).get("list") or []) if isinstance(data, dict) else []
            for item in items[:30]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                if not title or title in seen_titles:
                    continue
                tag = _match_tag(title)
                if tag:
                    seen_titles.add(title)
                    collected.append(CircleTopic(title=title[:48], source=src_label, tag=tag, fetched_at=now))
                    if len(collected) >= max_items:
                        break
        except Exception:
            pass

    return collected


class CircleRadarStore:
    """Persists and matches cached circle-specific hot topics."""

    def __init__(
        self,
        data_dir: Path | str,
        *,
        tags: Iterable[str] = (),
        refresh_hours: float = 4.0,
        max_items: int = 8,
    ) -> None:
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "circle_radar.json"
        self.tags = [str(t or "").strip() for t in tags if str(t or "").strip()]
        self.refresh_seconds = max(1800.0, float(refresh_hours) * 3600.0)
        self.max_items = max(1, int(max_items))
        self.topics: list[CircleTopic] = []
        self._last_attempt: float = 0.0
        self._load()

    def update_config(self, *, tags: Iterable[str], refresh_hours: float, max_items: int) -> None:
        self.tags = [str(t or "").strip() for t in tags if str(t or "").strip()]
        self.refresh_seconds = max(1800.0, float(refresh_hours) * 3600.0)
        self.max_items = max(1, int(max_items))

    def needs_refresh(self, now_ts: float | None = None) -> bool:
        if not self.tags:
            return False
        now = float(now_ts if now_ts is not None else time.time())
        return (now - self._last_attempt) >= self.refresh_seconds

    def refresh_sync(self, *, timeout_seconds: float = 5.0) -> list[CircleTopic]:
        self._last_attempt = time.time()
        fresh = fetch_circle_topics_sync(self.tags, max_items=self.max_items, timeout_seconds=timeout_seconds)
        if fresh:
            self.topics = fresh[: self.max_items]
            self._save()
        return self.topics

    def match_hint(self, text: str, *, now_ts: float | None = None) -> str:
        clean = str(text or "").strip().lower()
        if not clean or not self.topics:
            return ""
        now = float(now_ts if now_ts is not None else time.time())
        for topic in self.topics:
            if now - topic.fetched_at > 24 * 3600:
                continue
            if topic.tag.lower() in clean:
                return f"[圈层见闻] 你刚才刷手机看到个相关动态（{topic.source}）：「{topic.title}」，顺口可聊两句。"
        return ""

    def pick_icebreak_topic(self, *, now_ts: float | None = None) -> CircleTopic | None:
        now = float(now_ts if now_ts is not None else time.time())
        for topic in self.topics:
            if now - topic.fetched_at <= 24 * 3600:
                return topic
        return None

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            if isinstance(raw, list):
                self.topics = [CircleTopic(**item) for item in raw if isinstance(item, dict)]
        except Exception:
            self.topics = []

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([asdict(t) for t in self.topics], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self._path)
