import json
from typing import Any
from typing import Optional

from ddtrace._trace.sampling_rule import SamplingRule
from ddtrace._trace.span import Span
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class FilterRule(SamplingRule):
    """
    Definition of a filtering rule used to fully drop trace chunks matching a pattern.

    Reuses SamplingRule's glob matching and probability handling.
    """

    def __init__(
        self,
        filter_rate: float = 1.0,
        service: Optional[str] = None,
        name: Optional[str] = None,
        resource: Optional[str] = None,
        tags: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(sample_rate=filter_rate, service=service, name=name, resource=resource, tags=tags)

    @property
    def filter_rate(self) -> float:
        return self.sample_rate

    def should_drop(self, span: Span) -> bool:
        """Return whether this rule's deterministic draw says to drop the matching span's trace chunk."""
        return bool(super().sample(span))

    def __repr__(self) -> str:
        return (
            f"FilterRule(filter_rate={self.filter_rate}, service={self.service}, "
            f"name={self.name}, resource={self.resource}, tags={self.tags})"
        )

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, FilterRule):
            return False
        return (
            self.filter_rate == other.filter_rate
            and self.service == other.service
            and self.name == other.name
            and self.resource == other.resource
            and self.tags == other.tags
        )


def parse_filtering_rules(rules: str) -> list[FilterRule]:
    """Parse the trace filtering rules from a JSON string (DD_TRACE_FILTERING_RULES)."""
    filtering_rules: list[FilterRule] = []
    if not rules:
        return filtering_rules
    try:
        json_rules = json.loads(rules)
    except (json.JSONDecodeError, ValueError):
        log.error(
            "Failed to parse DD_TRACE_FILTERING_RULES=%r, no filtering rules will be applied",
            rules,
            exc_info=True,
            extra={"send_to_telemetry": False},
        )
        return []
    for rule in json_rules:
        try:
            rule_kwargs = dict(rule)
            filter_rate = rule_kwargs.pop("filter_rate", 1.0)
            filtering_rules.append(FilterRule(filter_rate=filter_rate, **rule_kwargs))
        except Exception:
            log.error(
                "Failed to apply filtering rule %r, skipping it",
                rule,
                exc_info=True,
                extra={"send_to_telemetry": False},
            )
    return filtering_rules
