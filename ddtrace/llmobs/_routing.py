"""Routing context for LLM Observability — multi-tenant routing and dual shipping.

A routing context redirects the LLMObs data created inside it to one or more destination
organizations, identified by API key. Two shapes fall out of the same mechanism:

* one target — the data goes to that org *instead of* the default one. This is what keeps a
  tenant's data out of the default org entirely, which query-time access controls cannot do.
* several targets — the same data is copied to each, i.e. dual shipping.

This lives in its own leaf module so that _writer.py can resolve a destination without
importing _llmobs.py, which would close an import cycle.
"""

import contextvars
from typing import Any
from typing import NamedTuple
from typing import Optional

from ddtrace.llmobs._constants import ROUTING_API_KEY
from ddtrace.llmobs._constants import ROUTING_SITE
from ddtrace.llmobs._constants import ROUTING_TARGETS


# A routing context as stored on the contextvar and stamped onto spans.
RoutingContextType = dict[str, list[dict[str, Any]]]


class RoutingTarget(NamedTuple):
    """A single resolved destination org."""

    api_key: str
    site: Optional[str] = None


_ROUTING_CONTEXTVAR: contextvars.ContextVar[Optional[RoutingContextType]] = contextvars.ContextVar(
    "dd_llmobs_routing_context", default=None
)


def get_routing_context() -> Optional[RoutingContextType]:
    """Return the active routing context, or None when data goes to the default org.

    The shape is ``{"targets": [{"api_key": ..., "site": ...}, ...]}``. ``site`` is omitted
    when the caller did not supply one, so the writer falls back to the configured site.
    """
    return _ROUTING_CONTEXTVAR.get()


def build_routing_context(
    dd_api_key: Optional[str] = None,
    dd_site: Optional[str] = None,
    targets: Optional[list[dict[str, Any]]] = None,
) -> RoutingContextType:
    """Validate the caller's arguments and normalize them into a routing context.

    A single dd_api_key is normalized into a one-entry targets list so downstream code only
    ever deals with the list form.

    :raises ValueError: If neither or both forms are given, or a target has no API key.
    """
    if dd_api_key is not None and targets is not None:
        raise ValueError("Cannot specify both dd_api_key and targets")
    if targets is None:
        if dd_api_key is None:
            raise ValueError("dd_api_key is required when targets is not provided")
        if not dd_api_key:
            raise ValueError("dd_api_key is required and must be non-empty")
        target: dict[str, Any] = {ROUTING_API_KEY: dd_api_key}
        if dd_site:
            target[ROUTING_SITE] = dd_site
        return {ROUTING_TARGETS: [target]}

    if not targets:
        raise ValueError("targets must contain at least one destination")
    normalized = []
    for entry in targets:
        api_key = entry.get("dd_api_key")
        if not api_key:
            raise ValueError("dd_api_key is required for every entry in targets")
        normalized_target: dict[str, Any] = {ROUTING_API_KEY: api_key}
        site = entry.get("dd_site")
        if site:
            normalized_target[ROUTING_SITE] = site
        normalized.append(normalized_target)
    return {ROUTING_TARGETS: normalized}


def routing_targets(context: Optional[RoutingContextType]) -> list[RoutingTarget]:
    """Convert a routing context into the destinations a writer should send to."""
    if not context:
        return []
    return [
        RoutingTarget(api_key=t[ROUTING_API_KEY], site=t.get(ROUTING_SITE)) for t in context.get(ROUTING_TARGETS, [])
    ]
