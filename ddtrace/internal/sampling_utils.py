import json
import os
from typing import TYPE_CHECKING

from ddtrace.sampler import SamplingRule


if TYPE_CHECKING:  # pragma: no cover
    from typing import List
    from typing import Optional

    from ddtrace.context import Context  # noqa
    from ddtrace.span import Span  # noqa

try:
    from json.decoder import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore


def get_trace_sampling_rules():
    # type:() -> Optional[List[SamplingRule]]
    # Ensure rules is a list
    rules = []  # type: List[SamplingRule]
    if rules is None:
        env_sampling_rules = os.getenv("DD_TRACE_SAMPLING_RULES")
        if env_sampling_rules:
            rules = _parse_rules_from_env_variable(env_sampling_rules)
        else:
            rules = []
    # Validate that the rules is a list of SampleRules
    for rule in rules:
        if not isinstance(rule, SamplingRule):
            raise TypeError("Rule {!r} must be a sub-class of type ddtrace.sampler.SamplingRules".format(rule))
    return rules


def _parse_rules_from_env_variable(rules):
    sampling_rules = []
    if rules is not None:
        json_rules = []
        try:
            json_rules = json.loads(rules)
        except JSONDecodeError:
            raise ValueError("Unable to parse DD_TRACE_SAMPLING_RULES={}".format(rules))
        for rule in json_rules:
            if "sample_rate" not in rule:
                raise KeyError("No sample_rate provided for sampling rule: {}".format(json.dumps(rule)))
            sample_rate = float(rule["sample_rate"])
            service = rule.get("service", SamplingRule.NO_RULE)
            name = rule.get("name", SamplingRule.NO_RULE)
            resource = rule.get("resource", SamplingRule.NO_RULE)
            tags = rule.get("tags", SamplingRule.NO_RULE)
            try:
                sampling_rule = SamplingRule(
                    sample_rate=sample_rate, service=service, name=name, resource=resource, tags=tags
                )
            except ValueError as e:
                raise ValueError("Error creating sampling rule {}: {}".format(json.dumps(rule), e))
            sampling_rules.append(sampling_rule)
    return sampling_rules
