from typing import Dict, Type
from .base import RouterBase
from .ddwrt import DDWRTRouter

ROUTER_HANDLERS: Dict[str, Type[RouterBase]] = {
    "ddwrt_v3_netgear_r7000": DDWRTRouter,
    "ddwrt": DDWRTRouter,
}


def get_router_handler(router_type: str) -> RouterBase:
    handler_class = ROUTER_HANDLERS.get(router_type)
    if handler_class is None:
        handler_class = DDWRTRouter
    return handler_class()
