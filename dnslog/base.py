from abc import ABC, abstractmethod
from typing import List, Dict, Any


class DnsLogBase(ABC):
    """Abstract base class for DNS-log handlers.

    Per-client summary queries (return ``[{"ip": ..., "count": ...}]``):

    * ``get_dns_lookups`` - per-client counts of DNS lookups that were not
      blocked (forwarded, cached, etc.).
    * ``get_dns_blocks``  - per-client counts of DNS queries that were blocked
      (gravity, regex, denylist, ...).

    Per-domain queries (return ``[{"domain": ..., "count": ...}]``):

    * ``get_dns_blocks_by_domain`` - counts of blocks grouped by the blocked
      domain (the queried address), across all clients.
    * ``get_dns_lookups_for_client`` - top looked-up domains for a specific
      client IP.
    * ``get_dns_blocks_for_client`` - top blocked domains for a specific
      client IP.

    ``period`` is a short string token e.g. ``"1h"``, ``"24h"``, ``"7d"``.
    All lists are sorted by count descending.
    """

    @abstractmethod
    def get_dns_lookups(self, conn, period: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_dns_blocks(self, conn, period: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_dns_blocks_by_domain(self, conn, period: str) -> List[Dict[str, Any]]:
        """Return per-domain block counts across all clients."""
        pass

    @abstractmethod
    def get_dns_lookups_for_client(self, conn, period: str, client_ip: str) -> List[Dict[str, Any]]:
        """Return per-domain lookup counts for a specific client IP."""
        pass

    @abstractmethod
    def get_dns_blocks_for_client(self, conn, period: str, client_ip: str) -> List[Dict[str, Any]]:
        """Return per-domain block counts for a specific client IP."""
        pass