"""Dynamic adapter API discovery without hardcoding third-party plugin IDs."""

from __future__ import annotations

from typing import Any


class DynamicAdapterBridge:
    """Discovers available OneBot/QQ adapter APIs at runtime via ctx.api.list()."""

    def __init__(self, *, override_call_api: str = "") -> None:
        self.override_call_api = str(override_call_api or "").strip()
        self._discovered = False
        self._generic_call_api: str = ""
        self._direct_apis: dict[str, str] = {}
        self._unsupported_actions: set[str] = set()

    def set_override(self, api_name: str) -> None:
        self.override_call_api = str(api_name or "").strip()

    def parse_api_list(self, payload: Any) -> None:
        items = payload.get("apis", []) if isinstance(payload, dict) else (payload if isinstance(payload, list) else [])
        self._generic_call_api = ""
        self._direct_apis.clear()

        for entry in items:
            if not isinstance(entry, dict):
                continue
            pid = str(entry.get("plugin_id") or "").strip()
            name = str(entry.get("name") or "").strip()
            if not pid or not name:
                continue
            full = name if name.startswith(pid + ".") else f"{pid}.{name}"

            if name == "action.call" or name.endswith(".action.call"):
                if not self._generic_call_api:
                    self._generic_call_api = full
            for short in ("send_poke", "delete_msg", "set_online_status", "set_input_status"):
                if name == short or name.endswith("." + short):
                    self._direct_apis.setdefault(short, full)

        self._discovered = True

    def resolve_call_target(self, action_name: str) -> tuple[str, str]:
        """Return (mode, api_name) where mode is 'generic', 'direct', or 'none'."""
        if action_name in self._unsupported_actions:
            return "none", ""
        if self.override_call_api:
            return "generic", self.override_call_api
        if self._generic_call_api:
            return "generic", self._generic_call_api
        if action_name in self._direct_apis:
            return "direct", self._direct_apis[action_name]
        return "none", ""

    async def ensure_discovered(self, api_cap: Any) -> None:
        if self._discovered or self.override_call_api:
            return
        try:
            res = await api_cap.list()
            self.parse_api_list(res)
        except Exception:
            self._discovered = True

    async def call_action(
        self,
        api_cap: Any,
        action_name: str,
        params: dict[str, Any],
    ) -> tuple[bool, Any]:
        await self.ensure_discovered(api_cap)
        mode, api_name = self.resolve_call_target(action_name)
        if mode == "none" or not api_name:
            return False, None

        try:
            if mode == "generic":
                resp = await api_cap.call(api_name, action_name=action_name, params=params)
            else:
                if action_name in ("set_online_status", "set_input_status"):
                    resp = await api_cap.call(api_name, params=params)
                else:
                    resp = await api_cap.call(api_name, **params)
        except Exception:
            return False, None

        if isinstance(resp, dict) and resp.get("success") is False:
            err_txt = str(resp.get("error") or "")
            if any(k in err_txt.lower() for k in ("unsupported", "unknown action", "not found", "未实现")):
                self._unsupported_actions.add(action_name)
            return False, resp
        inner = resp.get("result") if isinstance(resp, dict) and "result" in resp else resp
        if isinstance(inner, dict) and inner.get("retcode") not in (0, None) and inner.get("status") not in ("ok", None):
            return False, inner
        return True, inner
