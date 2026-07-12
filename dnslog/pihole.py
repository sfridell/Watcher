import time
from typing import Dict, Any, List, Optional

import requests

from .base import DnsLogBase
from .mock import period_seconds

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# Pi-hole v6 query statuses that indicate the query was blocked. Statuses not
# in this set are treated as permitted lookups (FORWARDED, CACHE, ...).
BLOCKED_STATUSES = {
    "GRAVITY",
    "GRAVITY_NXRA",
    "REGEX",
    "REGEX_NXRA",
    "DENYLIST",
    "EXTERNAL_BLOCKED_IP",
    "EXTERNAL_BLOCKED_NULL",
    "EXTERNAL_BLOCKED_NXRA",
    "GRAVITY_CNAME",
    "REGEX_CNAME",
    "DENYLIST_CNAME",
    "SPECIAL_DOMAIN",
}

PAGE_SIZE = 1000


def _split_host_port(ip_field: str, default_port: int = 80):
    """Allow callers to pass ``192.168.1.50`` or ``192.168.1.50:8080``."""
    if ":" in ip_field and not ip_field.startswith("["):
        host, _, port = ip_field.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            return ip_field, default_port
    return ip_field, default_port


class PiHoleDnsLog(DnsLogBase):
    """DNS-log adapter for Pi-hole v6 REST API (session-based auth).

    The ``conn`` argument expected by the methods is the ``dns_log`` dict from
    the connection profile, optionally augmented with a decrypted ``apikey``:

        {
            "type": "pihole",
            "ip": "192.168.12.50",     # or "host:port"
            "apikey": "<web/app password>",
            "scheme": "http",          # optional, default http
        }
    """

    def __init__(self):
        self._sid: Optional[str] = None
        self._csrf: Optional[str] = None

    # -- HTTP plumbing ------------------------------------------------
    def _base_url(self, conn) -> str:
        scheme = conn.get("scheme") or "http"
        host, port = _split_host_port(conn["ip"])
        return f"{scheme}://{host}:{port}/api"

    def _login(self, conn):
        url = f"{self._base_url(conn)}/auth"
        resp = requests.post(
            url,
            json={"password": conn["apikey"]},
            timeout=15,
            verify=False,
        )
        if resp.status_code != 200:
            raise Exception(
                f"Pi-hole auth failed (HTTP {resp.status_code}): {resp.text}"
            )
        data = resp.json()
        session = data.get("session", {})
        if not session.get("valid"):
            raise Exception("Pi-hole auth failed: session not valid")
        self._sid = session.get("sid")
        self._csrf = session.get("csrf")

    def _headers(self) -> Dict[str, str]:
        h = {}
        if self._sid:
            h["X-FTL-SID"] = self._sid
        if self._csrf:
            h["X-FTL-CSRF"] = self._csrf
        return h

    def _get(self, conn, path, params=None):
        if self._sid is None:
            self._login(conn)
        url = f"{self._base_url(conn)}{path}"
        resp = requests.get(
            url,
            params=params,
            headers=self._headers(),
            timeout=30,
            verify=False,
        )
        if resp.status_code == 401:
            # session expired - re-login and retry once
            self._sid = None
            self._login(conn)
            resp = requests.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=30,
                verify=False,
            )
        if resp.status_code != 200:
            raise Exception(
                f"Pi-hole GET {path} failed (HTTP {resp.status_code}): {resp.text}"
            )
        return resp.json()

    def _logout(self, conn):
        if not self._sid:
            return
        url = f"{self._base_url(conn)}/auth"
        try:
            requests.delete(url, headers=self._headers(), timeout=10, verify=False)
        except requests.RequestException:
            pass
        self._sid = None
        self._csrf = None

    # -- query aggregation -------------------------------------------
    def _iter_queries(self, conn, period: str):
        now = time.time()
        from_ts = now - period_seconds(period)
        cursor = None
        while True:
            params = {
                "from": int(from_ts),
                "until": int(now),
                "length": PAGE_SIZE,
                "disk": "true",
            }
            if cursor is not None:
                params["cursor"] = cursor
            data = self._get(conn, "/queries", params)
            queries = data.get("queries", []) or []
            for q in queries:
                yield q
            cursor = data.get("cursor")
            fetched = len(queries)
            if not cursor or fetched < PAGE_SIZE:
                break

    @staticmethod
    def _aggregate(queries, blocked: bool) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = {}
        for q in queries:
            status = q.get("status")
            is_blocked = status in BLOCKED_STATUSES
            if is_blocked != blocked:
                continue
            client = q.get("client") or {}
            ip = client.get("ip")
            if not ip:
                continue
            counts[ip] = counts.get(ip, 0) + 1
        return [
            {"ip": ip, "count": c}
            for ip, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    @staticmethod
    def _aggregate_by_domain(queries, blocked: bool, client_ip=None) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = {}
        for q in queries:
            status = q.get("status")
            is_blocked = status in BLOCKED_STATUSES
            if is_blocked != blocked:
                continue
            domain = q.get("domain")
            if not domain:
                continue
            if client_ip is not None:
                client = q.get("client") or {}
                ip = client.get("ip")
                if ip != client_ip:
                    continue
            counts[domain] = counts.get(domain, 0) + 1
        return [
            {"domain": d, "count": c}
            for d, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    # -- public interface --------------------------------------------
    def get_dns_lookups(self, conn, period: str) -> List[Dict[str, Any]]:
        queries = list(self._iter_queries(conn, period))
        return self._aggregate(queries, blocked=False)

    def get_dns_blocks(self, conn, period: str) -> List[Dict[str, Any]]:
        queries = list(self._iter_queries(conn, period))
        return self._aggregate(queries, blocked=True)

    def get_dns_blocks_by_domain(self, conn, period: str) -> List[Dict[str, Any]]:
        queries = list(self._iter_queries(conn, period))
        return self._aggregate_by_domain(queries, blocked=True)

    def get_dns_lookups_for_client(self, conn, period: str, client_ip: str) -> List[Dict[str, Any]]:
        queries = list(self._iter_queries(conn, period))
        return self._aggregate_by_domain(queries, blocked=False, client_ip=client_ip)

    def get_dns_blocks_for_client(self, conn, period: str, client_ip: str) -> List[Dict[str, Any]]:
        queries = list(self._iter_queries(conn, period))
        return self._aggregate_by_domain(queries, blocked=True, client_ip=client_ip)