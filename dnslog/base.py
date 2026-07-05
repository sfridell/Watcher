from abc import ABC, abstractmethod
from typing import List, Dict, Any


class DnsLogBase(ABC):
    """Abstract base class for DNS-log handlers.

    A DNS-log handler exposes two read-only queries over a time window:

    * ``get_dns_lookups`` - per-client counts of DNS lookups that were not
      blocked (forwarded, cached, etc.).
    * ``get_dns_blocks``  - per-client counts of DNS queries that were blocked
      (gravity, regex, denylist, ...).

    Both return a list of ``{"ip": <client_ip>, "count": <int>}`` dicts sorted
    by count descending. ``period`` is a short string token e.g. ``"1h"``,
    ``"24h"``, ``"7d"``.
    """

    @abstractmethod
    def get_dns_lookups(self, conn, period: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_dns_blocks(self, conn, period: str) -> List[Dict[str, Any]]:
        pass