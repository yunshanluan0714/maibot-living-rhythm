"""Unit tests for maibot-living-rhythm v2.0.0 pure logic modules."""

from datetime import datetime, timedelta, timezone
import unittest

from adapter_bridge import DynamicAdapterBridge
from link_preview import LinkPreviewCache, extract_previewable_urls, is_safe_public_url, parse_html_summary
from micro_actions import resolve_presence_for_phase
from schedule_engine import resolve_schedule_state


class LivingRhythmTests(unittest.TestCase):
    def test_dynamic_adapter_discovery(self) -> None:
        bridge = DynamicAdapterBridge()
        bridge.parse_api_list(
            {
                "success": True,
                "apis": [
                    {"plugin_id": "any-team.napcat-adapter", "name": "adapter.napcat.action.call", "version": "1"},
                    {"plugin_id": "any-team.napcat-adapter", "name": "adapter.napcat.message.send_poke", "version": "1"},
                ],
            }
        )
        mode, target = bridge.resolve_call_target("set_online_status")
        self.assertEqual(mode, "generic")
        self.assertEqual(target, "any-team.napcat-adapter.adapter.napcat.action.call")

        bridge2 = DynamicAdapterBridge()
        bridge2.parse_api_list(
            {"apis": [{"plugin_id": "other.adapter", "name": "message.send_poke", "version": "1"}]}
        )
        mode2, target2 = bridge2.resolve_call_target("send_poke")
        self.assertEqual(mode2, "direct")
        self.assertEqual(target2, "other.adapter.message.send_poke")

    def test_schedule_zero_daytime_prompt_bloat(self) -> None:
        tz = timezone(timedelta(hours=8))
        ts_sleep = datetime(2026, 11, 25, 3, 15, tzinfo=tz).timestamp()
        ts_morning = datetime(2026, 11, 25, 10, 15, tzinfo=tz).timestamp()
        ts_evening = datetime(2026, 11, 25, 20, 30, tzinfo=tz).timestamp()

        st_sleep = resolve_schedule_state(now_ts=ts_sleep)
        self.assertTrue(st_sleep.is_sleep)
        self.assertEqual(st_sleep.phase, "SLEEP")

        st_morning = resolve_schedule_state(now_ts=ts_morning)
        self.assertFalse(st_morning.is_sleep)
        self.assertEqual(st_morning.phase, "MORNING_SLACK")
        self.assertEqual(st_morning.hint, "")

        st_evening = resolve_schedule_state(now_ts=ts_evening)
        self.assertFalse(st_evening.is_sleep)
        self.assertEqual(st_evening.hint, "")

    def test_presence_phase_mapping(self) -> None:
        self.assertEqual(resolve_presence_for_phase("SLEEP").label, "睡觉中")
        self.assertEqual(resolve_presence_for_phase("SLEEP").ext_status, 1016)
        self.assertEqual(resolve_presence_for_phase("MORNING_SLACK").label, "学习中")
        self.assertEqual(resolve_presence_for_phase("LUNCH").label, "干饭中")
        self.assertEqual(resolve_presence_for_phase("LATE_DROWSY").status, 30)

    def test_link_preview_extract_ssrf_and_cache(self) -> None:
        text = "看看这篇 https://mp.weixin.qq.com/s/demo123 和这个 https://www.bilibili.com/video/BV1xx"
        urls = extract_previewable_urls(text)
        self.assertEqual(urls, ["https://mp.weixin.qq.com/s/demo123"])

        self.assertFalse(is_safe_public_url("http://127.0.0.1:8080/admin"))
        self.assertFalse(is_safe_public_url("http://192.168.1.1/login"))
        self.assertFalse(is_safe_public_url("http://localhost/test"))

        html = "<html><head><title>测试文章</title><meta name='description' content='这是一段长度超过二十四个字符的公开文章内容摘要用于测试提取逻辑。'></head><body>正文</body></html>"
        title, snippet = parse_html_summary(html)
        self.assertEqual(title, "测试文章")
        self.assertIn("公开文章内容摘要", snippet)

        cache = LinkPreviewCache(ttl_seconds=60)
        cache.set("https://example.com/a", "《示例》", now_ts=100.0)
        self.assertEqual(cache.get("https://example.com/a", now_ts=120.0), "《示例》")
        self.assertIsNone(cache.get("https://example.com/a", now_ts=200.0))


if __name__ == "__main__":
    unittest.main()
