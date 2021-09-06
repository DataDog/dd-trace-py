from typing import List
from typing import Optional
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


@attr.s(frozen=True)
class RuleMatchParameter(object):
    """The rule operator match parameter that detected an attack."""
    address = attr.ib(type=str)
    key_path = attr.ib(type=List[Union[str, int]], factory=list)
    value = attr.ib(type=Optional[str], default=None)


@attr.s(frozen=True)
class RuleMatch(object):
    """The rule operator result that detected an attack."""

    operator = attr.ib(type=str)
    operator_value = attr.ib(type=str)
    parameters = attr.ib(type=List[RuleMatchParameter], factory=list)
    highlight = attr.ib(type=List[str], factory=list)


@attr.s(frozen=True)
class Attack_0_1_0(object):
    """An event representing an AppSec Attack."""

    event_id = attr.ib(type=str)
    detected_at = attr.ib(type=str)
    rule = attr.ib(type=Rule)
    rule_match = attr.ib(type=RuleMatch)
    context = attr.ib(type=Union[Context_0_1_0])
    event_type = attr.ib(default="appsec.threat.attack")
    event_version = attr.ib(default="0.1.0")
