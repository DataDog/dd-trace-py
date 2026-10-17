"""
Utility functions for Redis integration tests.
"""

import socket
import time


def wait_for_cluster(host, port, client, timeout=20):
    """Wait until a reachable Redis-compatible cluster reports a healthy state."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        if sock.connect_ex((host, port)) != 0:
            return
    except Exception:
        return
    finally:
        sock.close()

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            info = client.execute_command("CLUSTER INFO")
            if info.get("cluster_state") == "ok":
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("Cluster at %s:%d not ready after %ds" % (host, port, timeout))


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
