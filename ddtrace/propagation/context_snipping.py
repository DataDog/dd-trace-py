from typing import Any
from typing import Dict
from typing import Optional
from typing import Union

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span


my_rule = {"SERVICE": {"EQUALS": "postgres"}, "CHILD_OF": {"NAME": {"CONTAINS": "myapp.multi-operation"}}}
my_rule_2 = {"SERVICE": {"EQUALS": "requests"}, "CHILD_OF": {"NAME": {"CONTAINS": "myapp.multi-operation"}}}
my_rule_3 = {"SERVICE": {"EQUALS": "requests"}, "CHILD_OF": {"NAME": {"CONTAINS": "__main__"}}}
my_rule_4 = {"SERVICE": {"EQUALS": "postgres"}, "CHILD_OF": {"NAME": {"CONTAINS": "__main__"}}}


SNIPPING_RULES = [my_rule, my_rule_2, my_rule_3, my_rule_4]


def apply_condition(value: Any, condition: Dict[str, Any]) -> bool:
    if "EQUALS" in condition:
        return value == condition["EQUALS"]
    elif "CONTAINS" in condition:
        return condition["CONTAINS"] in value
    return False


def context_snipping_matcher(
    name: str,
    child_of: Optional[Union[Span, Context]] = None,
    service: Optional[str] = None,
    resource: Optional[str] = None,
    span_type: Optional[str] = None,
    snipping_rules=SNIPPING_RULES,
    require_child_of=True,
) -> bool:
    # Iterate over each rule in the dictionary
    for rule in snipping_rules:
        if apply_condition(rule, service, name, resource):
            if "CHILD_OF" in rule:
                if not child_of:
                    return False

                if isinstance(child_of, Span):
                    # Recursively evaluate the child context matcher
                    if context_snipping_matcher(
                        name=getattr(child_of, "name", None),
                        child_of=None,  # Do not recurse further; only one level deep
                        service=child_of.service,
                        resource=child_of.resource,
                        span_type=child_of.span_type,
                        snipping_rules=[rule["CHILD_OF"]],
                        require_child_of=False,
                    ):
                        return True
            else:
                return not require_child_of

    return False


def rule_match(rule, service, name, resource):
    for key, condition in rule.items():
        if key == "NAME":
            if apply_condition(name, condition):
                return True
        elif key == "SERVICE":
            if apply_condition(service, condition):
                return True
        elif key == "RESOURCE":
            if apply_condition(resource, condition):
                return True
    return False
