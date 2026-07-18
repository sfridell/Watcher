from typing import Dict, Optional, Type
from .base import RouterBase
from .ddwrt import DDWRTRouter
from .mock import MockRouter
from .openwrt import OpenWrtRouter

ROUTER_HANDLERS: Dict[str, Type[RouterBase]] = {
    "ddwrt_v3_netgear_r7000": DDWRTRouter,
    "ddwrt": DDWRTRouter,
    "openwrt": OpenWrtRouter,
    "openwrt_x86": OpenWrtRouter,
    "mock": MockRouter,
}


def get_router_handler(router_type: str, name: Optional[str] = None) -> RouterBase:
    """Return a router handler instance for the given type. For 'mock' type, pass a name to enable state persistence."""
    handler_class = ROUTER_HANDLERS.get(router_type)
    if handler_class is None:
        handler_class = DDWRTRouter
    if router_type == "mock" and name is not None:
        return handler_class(name=name)
    return handler_class()
