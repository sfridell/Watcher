"""Pi-hole v5.x (lighttpd PHP API) DNS-log adapter.

Supports two authentication modes, auto-detected on first request:

1. **API token** (recommended) — pass the v5 API token (from *Settings → API*)
   as the ``apikey``. The adapter appends ``?auth=<token>`` to every API call.
   No login/session needed; the token is revocable from the admin UI.
2. **Web password** (fallback) — pass the web dashboard password as the
   ``apikey``. The adapter POSTs to ``/admin/login.php``, harvests the
   ``PHPSESSID`` cookie, and uses it for subsequent requests.

Auto-detection: on the first API call, the adapter tries ``?auth=<apikey>``.
If the response is a non-empty JSON dict, token mode is used for all
subsequent calls. If the response is ``[]`` (unauthenticated), the adapter
falls back to web-password session login.

Lookups-vs-blocks split:
    ``getAllQueries`` rows are 12-tuples whose column 4 carries a status
    string. Values reported by Pi-hole v5 include::

        1  = blocked (gravity)              9  = blocked (gravity CNAME)
        2  = OK (forwarded)                 10 = blocked (regex CNAME)
        3  = OK (cached)                    11 = blocked (blacklist CNAME)
        4  = blocked (regex)                12 = OK (retried)
        5  = blocked (blacklist)           13 = OK (ignored)
        6  = blocked (external IP)
        7  = blocked (external NULL)
        8  = blocked (external NXRA)
        14 = OK (retried)                  16 = unknown (other OK variant)

    We classify anything in ``BLOCKED_STATUSES`` (1, 4-11) as a block and
    anything in ``PERMITTED_STATUSES`` (2, 3) as a permitted lookup. Unknown
    codes are dropped from both aggregates to avoid mislabeling.

Hostname → IP resolution:
    ``getAllQueries`` returns whichever client identifier Pi-hole has on file
    — usually the resolved hostname. ``topClients`` returns ``"name|ip"``
    pairs which lets us translate the hostname back to an IP. Entries that
    Pi-hole never resolved come back as the bare IP.

Period handling:
    The v5 ``getAllQueries`` endpoint ignores ``from``/``until`` query
    parameters and only honors ``length`` (last N rows from the in-memory
    log, which covers ``MAXLOGAGE`` hours, default 24). The adapter therefore
    fetches a large ``N`` and applies a client-side timestamp filter for the
    requested period. The ``24h`` window matches the default in-memory log;
    ``1h`` is a strict client-side filter; ``7d`` only works when Pi-hole's
    ``MAXLOGAGE`` is configured to keep that much history in memory.
"""
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from .base import DnsLogBase
from .mock import period_seconds
from .pihole import _split_host_port

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

BLOCKED_STATUSES: Set[str] = {"1", "4", "5", "6", "7", "8", "9", "10", "11"}
PERMITTED_STATUSES: Set[str] = {"2", "3"}

# A very large N so the v5 server returns the whole in-memory log
# (server caps at MAXLOGAGE-window so this is safe).
FETCH_LENGTH = 1_000_000


class PiHoleV5DnsLog(DnsLogBase):
    """Pi-hole v5 adapter supporting both API-token and web-password auth.

    Auth mode is auto-detected on the first API call: the adapter tries
    ``?auth=<apikey>`` (token mode). If the server returns ``[]``
    (unauthenticated), it falls back to ``POST /admin/login.php`` with the
    apikey as the web password (session-cookie mode).
    """

    def __init__(self):
        self._session: Optional[requests.Session] = None
        self._auth_mode: Optional[str] = None  # 'token', 'session', or None (not yet determined)
        self._base_url: Optional[str] = None

    # -- session plumbing -------------------------------------------------
    def _ensure_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.verify = False
        return self._session

    def _ensure_base_url(self, conn):
        scheme = conn.get("scheme") or "http"
        host, port = _split_host_port(conn["ip"], default_port=80)
        self._base_url = f"{scheme}://{host}:{port}"

    def _login(self, conn):
        """Web-password login: POST to /admin/login.php, harvest PHPSESSID."""
        s = self._ensure_session()
        if self._base_url is None:
            self._ensure_base_url(conn)
        url = f"{self._base_url}/admin/login.php"
        resp = s.post(
            url,
            data={"pw": conn["apikey"], "persistentlogin": "on"},
            timeout=15,
        )
        if resp.status_code not in (200, 302):
            raise Exception(
                f"Pi-hole v5 web login failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        self._auth_mode = "session"

    def _api_get(self, conn, params):
        s = self._ensure_session()
        if self._base_url is None:
            self._ensure_base_url(conn)
        url = f"{self._base_url}/admin/api.php"

        # First call: try token auth, fall back to session login
        if self._auth_mode is None:
            p = dict(params)
            p["auth"] = conn["apikey"]
            resp = s.get(url, params=p, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data:
                    self._auth_mode = "token"
                    return data
            # token didn't work -> try web-password session login
            self._login(conn)

        # Subsequent calls or session mode
        p = dict(params)
        if self._auth_mode == "token":
            p["auth"] = conn["apikey"]
        resp = s.get(url, params=p, timeout=30)
        if resp.status_code != 200:
            # session may have expired — re-login and retry once
            if self._auth_mode == "session":
                self._login(conn)
                resp = s.get(url, params=p, timeout=30)
        if resp.status_code != 200:
            raise Exception(
                f"Pi-hole v5 query failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        data = resp.json()
        # Unauthenticated responses are `[]` (list); treat as empty dict
        if isinstance(data, list):
            return {}
        return data

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _build_name_to_ip_map(top_sources: Dict[str, int]) -> Tuple[Dict[str, str], Set[str]]:
        name_to_ip: Dict[str, str] = {}
        ip_only: Set[str] = set()
        for key in top_sources.keys():
            if "|" in key:
                name, ip = key.rsplit("|", 1)
                # only record the first IP per hostname (Pi-hole can have many)
                name_to_ip.setdefault(name, ip)
            else:
                # No hostname resolved; key is already an IP.
                ip_only.add(key)
        return name_to_ip, ip_only

    @staticmethod
    def _resolve_ip(client_key, name_to_ip, ip_only):
        if client_key in name_to_ip:
            return name_to_ip[client_key]
        if client_key in ip_only:
            return client_key
        # Unknown host: fall back to the raw key so the count is still surfaced
        return client_key

    @staticmethod
    def _sorted_results(ip_counts: Dict[str, int]) -> List[Dict[str, Any]]:
        return [
            {"ip": ip, "count": c}
            for ip, c in sorted(ip_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    # -- aggregation -----------------------------------------------------
    @staticmethod
    def _aggregate(rows, blocked: bool, from_ts: int, until_ts: int) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in rows:
            if not r or len(r) < 5:
                continue
            try:
                ts = int(float(r[0]))
            except (TypeError, ValueError):
                continue
            if ts < from_ts or ts > until_ts:
                continue
            status = r[4]
            is_blocked = status in BLOCKED_STATUSES
            if is_blocked != blocked:
                continue
            client = r[3]
            if not client:
                continue
            counts[client] = counts.get(client, 0) + 1
        return counts

    @staticmethod
    def _aggregate_by_domain(rows, blocked: bool, from_ts: int, until_ts: int,
                              client_keys=None) -> Dict[str, int]:
        """Aggregate query rows by domain (column 2).

        If ``client_keys`` is a set, only rows whose client field (column 3)
        matches one of the keys are included.
        """
        counts: Dict[str, int] = {}
        for r in rows:
            if not r or len(r) < 5:
                continue
            try:
                ts = int(float(r[0]))
            except (TypeError, ValueError):
                continue
            if ts < from_ts or ts > until_ts:
                continue
            status = r[4]
            is_blocked = status in BLOCKED_STATUSES
            if is_blocked != blocked:
                continue
            if client_keys is not None and r[3] not in client_keys:
                continue
            domain = r[2]
            if not domain:
                continue
            counts[domain] = counts.get(domain, 0) + 1
        return counts

    @staticmethod
    def _build_ip_to_names(top_sources: Dict[str, int]) -> Dict[str, Set[str]]:
        """Reverse map: IP → set of hostnames seen in topClients."""
        ip_to_names: Dict[str, Set[str]] = {}
        for key in top_sources.keys():
            if "|" in key:
                name, ip = key.rsplit("|", 1)
                ip_to_names.setdefault(ip, set()).add(name)
        return ip_to_names

    # -- public interface -----------------------------------------------
    def _query(self, conn, period: str, blocked: bool) -> List[Dict[str, Any]]:
        now = int(time.time())
        period_s = period_seconds(period)
        from_ts = now - period_s
        until_ts = now

        payload = self._api_get(conn, {"getAllQueries": FETCH_LENGTH})
        rows = payload.get("data", []) or []
        client_counts = self._aggregate(rows, blocked, from_ts, until_ts)

        # hostname -> IP map; failure here degrades gracefully to raw keys
        name_to_ip, ip_only = {}, set()
        try:
            tc = self._api_get(conn, {"topClients": "0"})
            top_sources = tc.get("top_sources", {}) or {}
            name_to_ip, ip_only = self._build_name_to_ip_map(top_sources)
        except Exception:
            pass

        ip_counts: Dict[str, int] = {}
        for client_key, c in client_counts.items():
            ip = self._resolve_ip(client_key, name_to_ip, ip_only)
            ip_counts[ip] = ip_counts.get(ip, 0) + c
        return self._sorted_results(ip_counts)

    def get_dns_lookups(self, conn, period: str) -> List[Dict[str, Any]]:
        return self._query(conn, period, blocked=False)

    def get_dns_blocks(self, conn, period: str) -> List[Dict[str, Any]]:
        return self._query(conn, period, blocked=True)

    def get_dns_blocks_by_domain(self, conn, period: str) -> List[Dict[str, Any]]:
        now = int(time.time())
        period_s = period_seconds(period)
        from_ts = now - period_s
        until_ts = now
        payload = self._api_get(conn, {"getAllQueries": FETCH_LENGTH})
        rows = payload.get("data", []) or []
        domain_counts = self._aggregate_by_domain(rows, True, from_ts, until_ts)
        return [
            {"domain": d, "count": c}
            for d, c in sorted(domain_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    def _query_for_client(self, conn, period: str, blocked: bool, client_ip: str):
        now = int(time.time())
        period_s = period_seconds(period)
        from_ts = now - period_s
        until_ts = now

        payload = self._api_get(conn, {"getAllQueries": FETCH_LENGTH})
        rows = payload.get("data", []) or []

        # Build IP→hostname reverse map so we can match client_ip against
        # the hostname column (column 3) in getAllQueries rows.
        client_keys = {client_ip}
        try:
            tc = self._api_get(conn, {"topClients": "0"})
            top_sources = tc.get("top_sources", {}) or {}
            ip_to_names = self._build_ip_to_names(top_sources)
            client_keys |= ip_to_names.get(client_ip, set())
        except Exception:
            pass

        domain_counts = self._aggregate_by_domain(
            rows, blocked, from_ts, until_ts, client_keys=client_keys)
        return [
            {"domain": d, "count": c}
            for d, c in sorted(domain_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    def get_dns_lookups_for_client(self, conn, period: str, client_ip: str):
        return self._query_for_client(conn, period, False, client_ip)

    def get_dns_blocks_for_client(self, conn, period: str, client_ip: str):
        return self._query_for_client(conn, period, True, client_ip)