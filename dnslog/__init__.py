from typing import Dict, Optional, Type

from .base import DnsLogBase
from .mock import MockDnsLog
from .pihole import PiHoleDnsLog
from .pihole_v5 import PiHoleV5DnsLog

DNS_LOG_HANDLERS: Dict[str, Type[DnsLogBase]] = {
    "pihole": PiHoleDnsLog,
    "pihole_v5": PiHoleV5DnsLog,
    "mock": MockDnsLog,
}


def get_dns_handler(dns_type: str, name: Optional[str] = None) -> DnsLogBase:
    """Return a DNS-log handler instance for the given type.

    For ``mock`` type, ``name`` is forwarded so the handler can persist its
    state to ``./mock_state/dns_<name>.json`` (mirrors ``routers.mock``).
    """
    handler_class = DNS_LOG_HANDLERS.get(dns_type)
    if handler_class is None:
        raise ValueError(f"unknown dns-log type '{dns_type}'")
    if dns_type == "mock" and name is not None:
        return handler_class(name=name)
    return handler_class()