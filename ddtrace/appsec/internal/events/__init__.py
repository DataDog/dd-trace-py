from typing import Union

from ddtrace.appsec.internal.events.attack import Attack_0_1_0
from ddtrace.appsec.internal.events.context import get_required_context


Event = Union[Attack_0_1_0]

__all__ = [
    "Attack_0_1_0",
    "Event",
    "get_required_context",
]
