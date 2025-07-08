import typing as t

from ddtrace import config
from ddtrace.internal.core.event_hub import dispatch
from ddtrace.internal.core.event_hub import on
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber


requires = ["remote-configuration"]


log = get_logger(__name__)


def _rc_callback(payloads: t.Sequence[Payload]) -> None:
    for payload in payloads:
        if payload.metadata is None or (content := payload.content) is None:
            continue

        if (service_target := t.cast(t.Optional[dict], content.get("service_target"))) is not None:
            if (service := t.cast(str, service_target.get("service"))) is not None and service != config.service:
                continue

            if (env := t.cast(str, service_target.get("env"))) is not None and env != config.env:
                continue

        if (lib_config := t.cast(dict, content.get("lib_config"))) is not None:
            dispatch("apm-tracing.rc", (lib_config, config))


class APMTracingAdapter(PubSub):
    __publisher_class__ = RemoteConfigPublisher
    __subscriber_class__ = RemoteConfigSubscriber
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self):
        self._publisher = self.__publisher_class__(self.__shared_data__)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, _rc_callback, "APM_TRACING")


def post_preload():
    pass


def start():
    if config._remote_config_enabled:
        from ddtrace.internal.products import manager
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.register(
            "APM_TRACING",
            APMTracingAdapter(),
            restart_on_fork=True,
            capabilities=[
                cap for product in manager.__products__.values() for cap in getattr(product, "APMCapabilities", [])
            ],
        )

        # Register remote config handlers
        for name, product in manager.__products__.items():
            if (rc_handler := getattr(product, "apm_tracing_rc", None)) is not None:
                on("apm-tracing.rc", rc_handler, name)


def restart(join=False):
    pass


def stop(join=False):
    if config._remote_config_enabled:
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.unregister("APM_TRACING")


def at_exit(join=False):
    stop(join=join)
