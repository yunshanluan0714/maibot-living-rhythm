"""Unit tests for maibot-living-rhythm v2.0.1 logic and frequency restoration."""

from datetime import datetime, timedelta, timezone
from email.message import Message
import importlib
import importlib.util
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from adapter_bridge import DynamicAdapterBridge
import link_preview as preview
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

    def test_connect_uses_validated_socket_address_and_checks_redirects(self) -> None:
        host = "article.example"
        endpoint = ("192.0.2.1", 443)
        addresses = [(preview.socket.AF_INET, preview.socket.SOCK_STREAM, preview.socket.IPPROTO_TCP, "", endpoint)]
        with patch.object(preview.socket, "socket") as socket_factory, \
             patch.object(preview.socket, "getaddrinfo", side_effect=AssertionError("unexpected second DNS lookup")), \
             patch.object(preview.ssl, "create_default_context") as tls:
            conn = preview._connect_public(
                preview.urllib.parse.urlsplit("https://" + host), host, 443, addresses, preview.time.monotonic() + 2
            )
            sock = socket_factory.return_value
            sock.connect.assert_called_once_with(endpoint)
            tls.return_value.wrap_socket.assert_called_once_with(sock, server_hostname=host)
            conn.close()

        url = "https://article.example/start"
        target = (preview.urllib.parse.urlsplit(url), "article.example", 443, [])
        redirect = MagicMock(status=302)
        redirect.getheader.return_value = "http://127.0.0.1/secret"
        with patch.object(preview, "_resolve_public_target", side_effect=[target, ValueError("non-public")]) as resolve, \
             patch.object(preview, "_connect_public") as connect:
            connect.return_value.getresponse.return_value.__enter__.side_effect = [redirect]
            self.assertEqual(preview.fetch_url_summary_sync(url), "")
            self.assertEqual([call.args[0] for call in resolve.call_args_list], [url, "http://127.0.0.1/secret"])
            self.assertEqual(connect.call_count, 1)


@unittest.skipUnless(importlib.util.find_spec("maibot_sdk"), "requires the real MaiBot SDK")
class LivingRhythmPluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        root = Path(__file__).resolve().parent
        pkg_name = "living_rhythm_sdk_test"
        spec = importlib.util.spec_from_file_location(
            pkg_name, root / "__init__.py", submodule_search_locations=[str(root)]
        )
        assert spec and spec.loader
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[pkg_name] = pkg
        spec.loader.exec_module(pkg)
        mod_spec = importlib.util.spec_from_file_location(pkg_name + ".plugin", root / "plugin.py")
        assert mod_spec and mod_spec.loader
        mod = importlib.util.module_from_spec(mod_spec)
        sys.modules[mod_spec.name] = mod
        mod_spec.loader.exec_module(mod)

        self.plugin = mod.LivingRhythmPlugin()
        self.plugin.set_plugin_config(self.plugin.build_default_config())
        self.set_adjust = AsyncMock(return_value={"success": True})
        self.get_adjust = AsyncMock(side_effect=lambda sid: {"success": True, "value": 0.65 if sid == "s1" else 1.25})
        self.get_group_streams = AsyncMock(return_value={"streams": [{"session_id": "s1"}, {"session_id": "s2"}]})
        self.plugin._set_context(
            SimpleNamespace(
                logger=logging.getLogger(__name__),
                chat=SimpleNamespace(get_group_streams=self.get_group_streams),
                frequency=SimpleNamespace(set_adjust=self.set_adjust, get_adjust=self.get_adjust),
                config=SimpleNamespace(get=AsyncMock(return_value="")),
            )
        )
        self.plugin._running = True

    async def test_sleep_frequency_restores_prior_values_and_retries_failures(self) -> None:
        # Mute both groups during sleep and record original values (0.65 and 1.25).
        await self.plugin._sync_sleep_frequency(True)
        self.assertEqual(self.plugin._muted_sessions, {"s1": 0.65, "s2": 1.25})
        self.assertTrue(self.plugin._sleep_freq_applied)

        # Wake up: s1 fails temporarily, s2 succeeds; s1 must stay queued for retry.
        self.set_adjust.reset_mock()
        self.set_adjust.side_effect = [RuntimeError("rpc busy"), {"success": True}]
        await self.plugin._sync_sleep_frequency(False)
        self.assertEqual(self.plugin._muted_sessions, {"s1": 0.65})
        self.assertTrue(self.plugin._sleep_freq_applied)

        # Next tick retries s1 and restores original 0.65 (not hardcoded 1.0).
        self.set_adjust.reset_mock()
        self.set_adjust.side_effect = None
        self.set_adjust.return_value = {"success": True}
        await self.plugin._sync_sleep_frequency(False)
        self.set_adjust.assert_awaited_once_with("s1", 0.65)
        self.assertEqual(self.plugin._muted_sessions, {})
        self.assertFalse(self.plugin._sleep_freq_applied)

    async def test_disabling_auto_adjust_while_muted_restores_sessions(self) -> None:
        await self.plugin._sync_sleep_frequency(True)
        self.assertEqual(set(self.plugin._muted_sessions), {"s1", "s2"})

        self.set_adjust.reset_mock()
        self.plugin.config.schedule.auto_adjust_frequency = False
        await self.plugin.on_config_update("self", {}, "test")
        self.assertEqual(self.plugin._muted_sessions, {})
        self.assertFalse(self.plugin._sleep_freq_applied)
        restored_calls = {(call.args[0], call.args[1]) for call in self.set_adjust.await_args_list}
        self.assertEqual(restored_calls, {("s1", 0.65), ("s2", 1.25)})


if __name__ == "__main__":
    unittest.main()
