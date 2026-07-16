import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from xml.sax.saxutils import escape as xml_escape


@dataclass
class TrackedElement:
    oc_id: str
    identity_key: str
    label: str
    xpath: str
    frame_id: Optional[str] = None


@dataclass
class XPathChangeAlert:
    oc_id: str
    label: str
    old_xpath: str
    new_xpath: str
    triggered_after: str  # e.g. "browser_click(o_25)"


class ElementMappingService:
    """
    DOM 元素映射服务。
    - stable_hash（identity_key + xpath 哈希）→ 分配/恢复 oc_id（保证唯一性）
    - identity_key（语义身份，不含 xpath）→ 追踪 xpath 变化（语义等价检测）
    同一 session 内多次 rescan，相同元素保持相同 oc_id。
    """

    _instance = None

    sessions_mapping: Dict[str, Dict[str, Any]]        # {oc_id: element_info}
    sessions_hash_map: Dict[str, Dict[str, str]]       # {stable_hash: oc_id}
    sessions_counters: Dict[str, int]                   # next_id_int
    sessions_tracked: Dict[str, Dict[str, Dict[str, TrackedElement]]]  # {identity_key: {oc_id: TrackedElement}}
    sessions_alerts: Dict[str, Dict[tuple, XPathChangeAlert]]
    sessions_last_action: Dict[str, str]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ElementMappingService, cls).__new__(cls)
            cls._instance.sessions_mapping = {}
            cls._instance.sessions_hash_map = {}
            cls._instance.sessions_counters = {}
            cls._instance.sessions_tracked = {}
            cls._instance.sessions_alerts = {}
            cls._instance.sessions_last_action = {}
        return cls._instance

    def _ensure_session(self, session_id: str):
        if session_id not in self.sessions_mapping:
            self.sessions_mapping[session_id] = {}
            self.sessions_hash_map[session_id] = {}
            self.sessions_counters[session_id] = 0
            self.sessions_tracked[session_id] = {}
            self.sessions_alerts[session_id] = {}
            self.sessions_last_action[session_id] = ""

    def get_or_create_oc_id(self, session_id: str, element_info: Dict[str, Any]) -> str:
        """
        用 stable_hash 查找已有 oc_id，找不到则分配新的 o_N。
        """
        self._ensure_session(session_id)
        stable_hash = element_info.get("stable_hash", "")
        hash_map = self.sessions_hash_map[session_id]

        if stable_hash in hash_map:
            return hash_map[stable_hash]

        current_id = self.sessions_counters[session_id]
        oc_id = f"o_{current_id}"
        self.sessions_counters[session_id] = current_id + 1
        hash_map[stable_hash] = oc_id
        return oc_id

    def register_element(self, session_id: str, oc_id: str, element_info: Dict[str, Any]):
        """
        Register/update element info.
        Uses identity_key (semantic) as the grouping key for tracked elements.
        Each identity_key may correspond to multiple oc_ids, so blueprint validation must
        flatten all grouped oc_ids instead of assuming identity_key is unique.
        """
        self._ensure_session(session_id)
        self.sessions_mapping[session_id][oc_id] = element_info

        identity_key = element_info.get("identityKey", "")
        if not identity_key:
            return

        tracked_group = self.sessions_tracked[session_id].get(identity_key)
        if not tracked_group or oc_id in tracked_group or len(tracked_group) != 1:
            return

        old = next(iter(tracked_group.values()))
        new_xpath = element_info.get("xpath", "")
        if not old.xpath or not new_xpath or old.xpath == new_xpath:
            return

        alert = XPathChangeAlert(
            oc_id=old.oc_id,
            label=old.label,
            old_xpath=old.xpath,
            new_xpath=new_xpath,
            triggered_after=self.sessions_last_action.get(session_id, "unknown"),
        )
        dedup_key = (identity_key, old.oc_id, old.xpath, new_xpath)
        self.sessions_alerts.setdefault(session_id, {})[dedup_key] = alert

    def track_element(self, session_id: str, oc_id: str):
        """Add an element to the tracked set after a successful interaction/query."""
        self._ensure_session(session_id)
        info = self.sessions_mapping[session_id].get(oc_id)
        if info is None:
            return
        identity_key = info.get("identityKey", "")
        if not identity_key:
            return
        tracked_group = self.sessions_tracked[session_id].setdefault(identity_key, {})
        tracked_group[oc_id] = TrackedElement(
            oc_id=oc_id,
            identity_key=identity_key,
            label=info.get("text", ""),
            xpath=info.get("xpath", ""),
            frame_id=info.get("frame_id")
        )

    def set_last_action(self, session_id: str, action_desc: str):
        self._ensure_session(session_id)
        self.sessions_last_action[session_id] = action_desc

    def get_and_clear_alerts(self, session_id: str) -> List[XPathChangeAlert]:
        return list(self.sessions_alerts.get(session_id, {}).values())

    def render_registry(self, session_id: str) -> Optional[str]:
        tracked = self.get_tracked_elements(session_id)
        if not tracked:
            return None

        alerts = self.get_and_clear_alerts(session_id)
        alerted_ids = {a.oc_id for a in alerts}

        lines = [f'<element_registry tracked="{len(tracked)}" alerts="{len(alerts)}">']
        lines.append('    <tracked_elements>')
        for _key, te in tracked.items():
            status_attr = ' status="changed"' if te.oc_id in alerted_ids else ''
            xpath_str = f"[Iframe: {te.frame_id}] {te.xpath}" if te.frame_id else te.xpath
            lines.append(
                f'        <el id="{xml_escape(te.oc_id)}" '
                f'label="{xml_escape(te.label)}" '
                f'xpath="{xml_escape(xpath_str)}"{status_attr} />'
            )
        lines.append('    </tracked_elements>')

        if alerts:
            lines.append('    <alerts>')
            for a in alerts:
                lines.append(
                    f'        <xpath_changed id="{xml_escape(a.oc_id)}" '
                    f'label="{xml_escape(a.label)}" '
                    f'after="{xml_escape(a.triggered_after)}">'
                )
                lines.append(f'            before: {a.old_xpath}')
                lines.append(f'            after:  {a.new_xpath}')
                lines.append('        </xpath_changed>')
            lines.append('    </alerts>')

        lines.append('</element_registry>')
        return '\n'.join(lines)

    def get_tracked_elements(self, session_id: str) -> Dict[str, TrackedElement]:
        """Return tracked elements keyed by oc_id (for blueprint validation)."""
        tracked = self.sessions_tracked.get(session_id, {})
        return {
            oc_id: te
            for tracked_group in tracked.values()
            for oc_id, te in tracked_group.items()
        }

    def get_tracked_summary(self, session_id: str) -> List[Dict[str, Any]]:
        tracked = self.get_tracked_elements(session_id)
        alerts = self.sessions_alerts.get(session_id, {})
        alert_counts: Dict[str, int] = {}
        for alert in alerts.values():
            alert_counts[alert.oc_id] = alert_counts.get(alert.oc_id, 0) + 1

        return [
            {
                "oc_id": te.oc_id,
                "label": te.label,
                "xpath": f"[Iframe: {te.frame_id}] {te.xpath}" if te.frame_id else te.xpath,
                "change_count": alert_counts.get(te.oc_id, 0),
            }
            for te in tracked.values()
        ]

    def get_mapping(self, session_id: str, oc_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_session(session_id)
        return self.sessions_mapping[session_id].get(oc_id)

    def get_xpath(self, session_id: str, oc_id: str) -> str:
        self._ensure_session(session_id)
        info = self.sessions_mapping[session_id].get(oc_id)
        if info and "xpath" in info:
            xpath = info["xpath"]
            frame_id = info.get("frame_id")
            if frame_id:
                return f"[Iframe: {frame_id}] {xpath}"
            return xpath
        return f"/* not found {oc_id} xpath */"

    def clear_mapping(self, session_id: str):
        for store in (
            self.sessions_mapping, self.sessions_hash_map, self.sessions_counters,
            self.sessions_tracked, self.sessions_alerts, self.sessions_last_action,
        ):
            store.pop(session_id, None)


element_mapping_service = ElementMappingService()
