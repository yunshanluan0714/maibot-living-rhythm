"""Safe web link preview fetcher with SSRF guard, configurable skip domains, and TTL cache."""

from __future__ import annotations

from collections import OrderedDict
from html import unescape
from html.parser import HTMLParser
import ipaddress
import re
import socket
import time
from typing import Iterable
import urllib.parse
import urllib.request

_URL_RE = re.compile(r"https?://[^\s<>\"'\]）)]+", re.I)
DEFAULT_SKIP_HOSTS: tuple[str, ...] = (
    "bilibili.com",
    "b23.tv",
    "douyin.com",
    "v.douyin.com",
    "iesdouyin.com",
)


class _HTMLSummaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.skip_depth = 0
        self.title_parts: list[str] = []
        self.meta_desc: str = ""
        self.body_parts: list[str] = []
        self._body_len = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg"):
            self.skip_depth += 1
            return
        if t == "title":
            self.in_title = True
        elif t == "meta":
            attr_map = {k.lower(): (v or "") for k, v in attrs}
            key = (attr_map.get("name") or attr_map.get("property") or "").lower()
            if key in ("description", "og:description", "twitter:description") and not self.meta_desc:
                self.meta_desc = attr_map.get("content", "").strip()

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg") and self.skip_depth > 0:
            self.skip_depth -= 1
        elif t == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.skip_depth > 0:
            return
        clean = " ".join(data.split())
        if not clean:
            return
        if self.in_title:
            self.title_parts.append(clean)
        elif self._body_len < 600:
            self.body_parts.append(clean)
            self._body_len += len(clean) + 1


def extract_previewable_urls(text: str, *, skip_domains: Iterable[str] = DEFAULT_SKIP_HOSTS) -> list[str]:
    skip_list = tuple(str(d or "").strip().lower().lstrip(".") for d in skip_domains if str(d or "").strip())
    urls: list[str] = []
    for raw in _URL_RE.findall(str(text or "")):
        url = raw.rstrip(".,;:!?，。；：！？")
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        if not host:
            continue
        if any(host == skip or host.endswith("." + skip) for skip in skip_list):
            continue
        if url not in urls:
            urls.append(url)
    return urls[:2]


def is_safe_public_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host or host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        return False

    def _ip_unsafe(ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
            return (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            )
        except ValueError:
            return False

    if _ip_unsafe(host):
        return False
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        for info in infos:
            addr = info[4][0]
            if _ip_unsafe(addr):
                return False
    except Exception:
        return False
    return True


def parse_html_summary(html_text: str, *, max_chars: int = 360) -> tuple[str, str]:
    parser = _HTMLSummaryParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        pass
    title = unescape(" ".join(parser.title_parts)).strip()[:100]
    desc = unescape(parser.meta_desc).strip()
    body = unescape(" ".join(parser.body_parts)).strip()
    snippet = desc if len(desc) >= 24 else (body or desc)
    snippet = " ".join(snippet.split())[:max_chars]
    return title, snippet


class LinkPreviewCache:
    """In-memory TTL + LRU cache for fetched webpage summaries."""

    def __init__(self, *, ttl_seconds: int = 1800, max_entries: int = 256) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_entries = max(16, int(max_entries))
        self._items: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def get(self, url: str, now_ts: float | None = None) -> str | None:
        now = float(now_ts if now_ts is not None else time.time())
        entry = self._items.get(url)
        if entry is None:
            return None
        val, ts = entry
        if now - ts > self.ttl_seconds:
            self._items.pop(url, None)
            return None
        self._items.move_to_end(url)
        return val

    def set(self, url: str, summary: str, now_ts: float | None = None) -> None:
        now = float(now_ts if now_ts is not None else time.time())
        self._items[url] = (summary, now)
        self._items.move_to_end(url)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)


def fetch_url_summary_sync(url: str, *, timeout_seconds: float = 5.0, max_chars: int = 360) -> str:
    if not is_safe_public_url(url):
        return ""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_seconds)) as resp:
            ctype = str(resp.headers.get("Content-Type") or "").lower()
            if ctype and not any(t in ctype for t in ("text/html", "application/xhtml", "text/plain")):
                return ""
            raw = resp.read(262144)
            charset = resp.headers.get_content_charset() or "utf-8"
            html_text = raw.decode(charset, errors="replace")
    except Exception:
        return ""

    title, snippet = parse_html_summary(html_text, max_chars=max_chars)
    if not title and not snippet:
        return ""
    if title and snippet and snippet != title:
        return f"《{title}》 - {snippet}"
    return f"《{title}》" if title else snippet
