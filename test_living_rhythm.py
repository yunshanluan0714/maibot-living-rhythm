"""Unit tests for maibot-living-rhythm pure logic modules."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import random
import tempfile
import time
import unittest

from adapter_bridge import DynamicAdapterBridge
from circle_radar import CircleRadarStore, CircleTopic
from micro_actions import (
    detect_group_poll,
    detect_group_solitaire,
    is_redpacket_message,
    plan_redpacket_reaction,
    resolve_presence_for_phase,
)
from reality_anchor import RealityAnchorManager, WeatherSnapshot, resolve_calendar_anchor
from schedule_engine import SleepCatchupStore, resolve_schedule_state, score_catchup_message


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

    def test_schedule_phases_and_catchup(self) -> None:
        tz = timezone(timedelta(hours=8))
        ts_sleep = datetime(2026, 11, 25, 3, 15, tzinfo=tz).timestamp()
        ts_wake = datetime(2026, 11, 25, 8, 10, tzinfo=tz).timestamp()
        self.assertTrue(resolve_schedule_state(now_ts=ts_sleep).is_sleep)
        self.assertEqual(resolve_schedule_state(now_ts=ts_wake).phase, "WAKE_CATCHUP")

        with tempfile.TemporaryDirectory() as td:
            store = SleepCatchupStore(Path(td))
            self.assertGreaterEqual(
                score_catchup_message("小助手明天几点集合？", is_private=False, is_at_bot=True, bot_aliases=["小助手"]),
                4.0,
            )
            store.record_sleep_message(
                session_id="qq:group:1",
                group_id="1",
                user_id="u1",
                sender_name="甲",
                message_id="m1",
                text="明天有安排吗？",
                score=5.0,
                created_at=1000.0,
            )
            picked = store.pop_catchup_targets(max_per_group=1, now_ts=1100.0)
            self.assertEqual(len(picked), 1)
            self.assertEqual(store.pending_count(), 0)

    def test_micro_actions_and_presence(self) -> None:
        self.assertEqual(resolve_presence_for_phase("SLEEP").label, "睡觉中")
        self.assertEqual(resolve_presence_for_phase("MORNING_SLACK").device_kind, "day")
        sol = detect_group_solitaire("#接龙 周六活动\n1. A\n2. B")
        self.assertIsNotNone(sol)
        assert sol is not None
        self.assertEqual(sol.next_index, 3)
        self.assertTrue(detect_group_poll("[群投票] 晚上吃啥"))
        self.assertTrue(is_redpacket_message("[QQ红包] 恭喜发财"))
        plan = plan_redpacket_reaction("[QQ红包] 8元", rng=random.Random(1), miss_prob=0.0)
        self.assertFalse(plan.should_miss)
        self.assertGreaterEqual(plan.delay_seconds, 2.0)

    def test_reality_anchor_and_circle_radar(self) -> None:
        rain = WeatherSnapshot(city="上海", condition="大雨", temp_c="12", fetched_at=100.0)
        mgr = RealityAnchorManager(city="上海")
        mgr.weather = rain
        self.assertIn("上海", mgr.build_hint(latest_text="外面下雨外卖好慢"))
        self.assertIn("调休", resolve_calendar_anchor(date(2026, 11, 28), adjusted_workdays=["2026-11-28"]))

        with tempfile.TemporaryDirectory() as td:
            radar = CircleRadarStore(Path(td), tags=["AI", "游戏"])
            now = time.time()
            radar.topics = [CircleTopic(title="AI开源项目更新", source="B站科技区", tag="AI", fetched_at=now)]
            self.assertIn("AI开源项目更新", radar.match_hint("最近AI圈有什么新闻", now_ts=now + 10))


if __name__ == "__main__":
    unittest.main()
