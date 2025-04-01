import enum

from ddtrace import config
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._pubsub import RemoteConfigSubscriber
from ddtrace.internal.remoteconfig.client import config as rc_config


class GlobalConfigPubSub(PubSub):
    __publisher_class__ = RemoteConfigPublisher
    __subscriber_class__ = RemoteConfigSubscriber
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self, callback):
        self._publisher = self.__publisher_class__(self.__shared_data__, None)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, "GlobalConfig")


class Capabilities(enum.IntFlag):
    APM_TRACING_SAMPLE_RATE = 1 << 12
    APM_TRACING_LOGS_INJECTION = 1 << 13
    APM_TRACING_HTTP_HEADER_TAGS = 1 << 14
    APM_TRACING_CUSTOM_TAGS = 1 << 15
    APM_TRACING_ENABLED = 1 << 19
    APM_TRACING_SAMPLE_RULES = 1 << 29


# TODO: Modularize better into their own respective components
def _register_rc_products() -> None:
    """Enable fetching configuration from Datadog."""
    from ddtrace.internal.flare.flare import Flare
    from ddtrace.internal.flare.handler import _handle_tracer_flare
    from ddtrace.internal.flare.handler import _tracerFlarePubSub
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    remoteconfig_pubsub = GlobalConfigPubSub(config._handle_remoteconfig)
    flare = Flare(trace_agent_url=config._trace_agent_url, api_key=config._dd_api_key, ddconfig=config.__dict__)
    tracerflare_pubsub = _tracerFlarePubSub()(_handle_tracer_flare, flare)
    remoteconfig_poller.register("APM_TRACING", remoteconfig_pubsub, capabilities=Capabilities)
    remoteconfig_poller.register("AGENT_CONFIG", tracerflare_pubsub)
    remoteconfig_poller.register("AGENT_TASK", tracerflare_pubsub)


def post_preload():
    pass


def start():
    if config._remote_config_enabled:
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.enable()
        _register_rc_products()


def restart(join=False):
    if config._remote_config_enabled:
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.reset_at_fork()


def stop(join=False):
    if config._remote_config_enabled:
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.disable(join=join)


def at_exit(join=False):
    if config._remote_config_enabled and not rc_config.skip_shutdown:
        stop(join=join)
