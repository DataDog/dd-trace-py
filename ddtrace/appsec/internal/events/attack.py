from typing import List
from typing import Union

import attr

from ddtrace.appsec.internal.events.context import Context_0_1_0


@attr.s(frozen=True)
class KV(object):
    """Key-value pair."""

    name = attr.ib(type=str)
    value = attr.ib(type=str)


@attr.s(frozen=True)
class Rule(object):
    """The rule that detected an attack."""

    id = attr.ib(type=str)
    name = attr.ib(type=str)
    set = attr.ib(type=str)


@attr.s(frozen=True)
class RuleMatch(object):
    """The rule operator result that detected an attack."""

    operator = attr.ib(type=str)
    operator_value = attr.ib(type=str)
    parameters = attr.ib(type=List[KV])
    highlight = attr.ib(type=List[str])
    has_server_side_match = attr.ib(type=bool)


@attr.s(frozen=True)
class Attack_0_1_0(object):
    """An event representing an AppSec Attack."""

    event_id = attr.ib(type=str)
    detected_at = attr.ib(type=str)
    type = attr.ib(type=str)
    blocked = attr.ib(type=bool)
    rule = attr.ib(type=Rule)
    rule_match = attr.ib(type=RuleMatch)
    context = attr.ib(type=Union[Context_0_1_0])
    event_type = attr.ib(default="appsec.threat.attack")
    event_version = attr.ib(default="0.1.0")
