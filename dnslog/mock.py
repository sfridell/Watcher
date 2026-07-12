import os
from typing import Dict, Any, List

from .base import DnsLogBase

_DEFAULT_STATE = {
    "lookups": {
        "192.168.1.10": 142,
        "192.168.1.11": 88,
        "192.168.1.20": 5,
    },
    "blocks": {
        "192.168.1.10": 12,
        "192.168.1.30": 3,
        "192.168.1.20": 1,
    },
    "blocked_domains": {
        "ads.evil.com": 50,
        "tracker.net": 30,
        "malware.org": 10,
    },
    "client_lookups": {
        "192.168.1.10": {"google.com": 80, "facebook.com": 62},
        "192.168.1.11": {"github.com": 88},
    },
    "client_blocks": {
        "192.168.1.10": {"ads.evil.com": 8, "tracker.net": 4},
        "192.168.1.20": {"malware.org": 1},
    },
}

_MOCK_STATE_DIR = "./mock_state"


def period_seconds(period: str) -> int:
    """Map a period token (e.g. ``"1h"``, ``"24h"``, ``"7d"``) to seconds."""
    p = (period or "").strip().lower()
    if not p:
        raise ValueError("period must be non-empty, e.g. '1h', '24h', '7d'")
    unit = p[-1]
    try:
        n = int(p[:-1])
    except ValueError:
        raise ValueError(f"invalid period '{period}'")
    if unit == "h":
        return n * 3600
    if unit == "d":
        return n * 86400
    if unit == "m":
        return n * 60
    if unit == "s":
        return n
    raise ValueError(f"unknown period unit '{unit}' in '{period}'")


class MockDnsLog(DnsLogBase):
    """In-memory DNS-log simulator mirroring :class:`routers.mock.MockRouter`.

    Persists per-client lookup/block counts to ``./mock_state/dns_<name>.json``.
    The ``period`` argument is accepted but ignored for the mock (the counts
    are static sample values).
    """

    def __init__(self, name=None, state=None):
        self._name = name
        if state is not None:
            self._state = state
        elif name is not None:
            self._load_state(name)
        else:
            self._state = {
                "lookups": dict(_DEFAULT_STATE["lookups"]),
                "blocks": dict(_DEFAULT_STATE["blocks"]),
                "blocked_domains": dict(_DEFAULT_STATE["blocked_domains"]),
                "client_lookups": {k: dict(v) for k, v in _DEFAULT_STATE["client_lookups"].items()},
                "client_blocks": {k: dict(v) for k, v in _DEFAULT_STATE["client_blocks"].items()},
            }

    def _state_path(self, name):
        return os.path.join(_MOCK_STATE_DIR, f"dns_{name}.json")

    def _load_state(self, name):
        path = self._state_path(name)
        if os.path.exists(path):
            import json
            with open(path, "r") as f:
                self._state = json.load(f)
        else:
            self._state = {
                k: (dict(v) if isinstance(v, dict) and all(isinstance(x, dict) for x in v.values())
                    else dict(v) if isinstance(v, dict) else v)
                for k, v in _DEFAULT_STATE.items()
            }
            # deep-copy nested dicts in client_lookups/client_blocks
            self._state["client_lookups"] = {
                k: dict(v) for k, v in _DEFAULT_STATE["client_lookups"].items()}
            self._state["client_blocks"] = {
                k: dict(v) for k, v in _DEFAULT_STATE["client_blocks"].items()}

    def _save_state(self):
        if self._name is None:
            return
        path = self._state_path(self._name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import json
        with open(path, "w") as f:
            json.dump(self._state, f, indent=2)

    @staticmethod
    def _sorted_counts(data: Dict[str, int]) -> List[Dict[str, Any]]:
        return [
            {"ip": ip, "count": count}
            for ip, count in sorted(data.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    @staticmethod
    def _sorted_domain_counts(data: Dict[str, int]) -> List[Dict[str, Any]]:
        return [
            {"domain": domain, "count": count}
            for domain, count in sorted(data.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    def get_dns_lookups(self, conn, period: str) -> List[Dict[str, Any]]:
        # validate period even though the mock ignores it
        period_seconds(period)
        return self._sorted_counts(self._state.get("lookups", {}))

    def get_dns_blocks(self, conn, period: str) -> List[Dict[str, Any]]:
        period_seconds(period)
        return self._sorted_counts(self._state.get("blocks", {}))

    def get_dns_blocks_by_domain(self, conn, period: str) -> List[Dict[str, Any]]:
        period_seconds(period)
        return self._sorted_domain_counts(self._state.get("blocked_domains", {}))

    def get_dns_lookups_for_client(self, conn, period: str, client_ip: str) -> List[Dict[str, Any]]:
        period_seconds(period)
        return self._sorted_domain_counts(
            self._state.get("client_lookups", {}).get(client_ip, {}))

    def get_dns_blocks_for_client(self, conn, period: str, client_ip: str) -> List[Dict[str, Any]]:
        period_seconds(period)
        return self._sorted_domain_counts(
            self._state.get("client_blocks", {}).get(client_ip, {}))