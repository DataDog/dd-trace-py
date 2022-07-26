import os
import typing as t

from envier import En


class AgentConfig(En):
    hostname = En.v(str, "agent.host", default="localhost")
    port = En.v(int, "agent.port", default=8126)
    stats_port = En.v(int, "dogstatsd_port", default=8125)
    _stats_url = En.v(
        t.Optional[str],
        "dogstatsd_url",
        default=None,
        help_type="URL",
        help_default="``unix:///var/run/datadog/dsd.socket`` if available otherwise ``udp://localhost:8125``",
        help="The URL to use to connect the Datadog agent for Dogstatsd metrics. The url can start with ``udp://`` "
        "to connect using UDP or with ``unix://`` to use a Unix Domain Socket. "
        "Example for UDP url: ``DD_TRACE_AGENT_URL=udp://localhost:8125``"
        "Example for UDS: ``DD_TRACE_AGENT_URL=unix:///var/run/datadog/dsd.socket``",
    )

    url = En.d(str, lambda c: "http://%s:%s" % (c.hostname, c.port))
    stats_url = En.d(
        str,
        lambda c: c._stats_url or "unix:///var/run/datadog/dsd.socket"
        if os.path.exists("/var/run/datadog/dsd.socket")
        else "udp://%s:%s" % (c.hostname, c.stats_port),
    )
