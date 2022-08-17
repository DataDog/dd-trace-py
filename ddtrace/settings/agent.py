import os
import typing as t

from envier import En


def _derive_stats_url(config):
    # type: (AgentConfig) -> str
    if config._stats_url is not None:
        return config._stats_url

    if config._stats_port is not None or config._hostname is not None:
        return "udp://{}:{}".format(config.hostname, config.stats_port)

    return (
        "unix:///var/run/datadog/dsd.socket"
        if os.path.exists("/var/run/datadog/dsd.socket")
        else "udp://%s:%s" % (config.hostname, config.stats_port)
    )


class AgentConfig(En):
    _hostname = En.v(t.Optional[str], "agent.host", default=None)
    hostname = En.d(str, lambda c: c._hostname or "localhost")

    port = En.v(int, "agent.port", default=8126)

    _stats_port = En.v(t.Optional[int], "dogstatsd_port", default=None)
    stats_port = En.d(int, lambda c: c._stats_port or 8125)

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

    stats_url = En.d(str, _derive_stats_url)
