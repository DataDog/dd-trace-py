# -*- coding: utf-8 -*-
"""
Utility functions for Redis integration tests.
"""


def find_redis_span(spans, resource=None, raw_command=None, component="redis", raw_command_tag="redis.raw_command"):
    """
    Helper function to find a Redis span from a list of spans.
    Filters spans by component tag, resource, and raw_command.

    :param spans: List of spans to search
    :param resource: Optional resource name to filter by
    :param raw_command: Optional raw command string to filter by (e.g., "GET cheese")
    :param component: Component tag value to filter by (default: "redis")
    :param raw_command_tag: Tag name to use for raw_command filtering (default: "redis.raw_command")
    :returns: The matching span
    """
    filtered = [s for s in spans if s.get_tag("component") == component]
    if resource:
        filtered = [s for s in filtered if s.resource == resource]
    if raw_command:
        filtered = [s for s in filtered if s.get_tag(raw_command_tag) == raw_command]
    assert len(filtered) == 1, (
        f"Expected exactly 1 matching span, got {len(filtered)}. "
        f"All spans: {[(s.resource, s.get_tag('component')) for s in spans]}"
    )
    return filtered[0]
