"""
Module providing a stats client to be used by the OpenAI integration.
"""
from ddtrace import config
from ddtrace.internal.agent import get_stats_url
from ddtrace.internal.dogstatsd import get_dogstatsd_client


_statsd = None


def stats_client():
    global _statsd
    if _statsd is None and config.openai.metrics_enabled:
        # FIXME: this currently does not consider if the tracer is configured to
        # use a different hostname. eg. tracer.configure(host="new-hostname")

        # FIXME: the dogstatsd client doesn't support multi-threaded usage
        _statsd = get_dogstatsd_client(
            get_stats_url(),
            namespace="openai",
            tags=["env:%s" % config.env, "service:%s" % config.service, "version:%s" % config.version],
        )

    # TODO: handle when metrics is disabled
    return _statsd
