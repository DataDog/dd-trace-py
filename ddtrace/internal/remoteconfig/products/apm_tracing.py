from collections import ChainMap
import enum
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


class APMCapabilities(enum.IntFlag):
    APM_TRACING_MULTICONFIG = 1 << 45


def config_key(payload: Payload) -> int:
    content = t.cast(dict, payload.content)

    service_target = t.cast(t.Optional[dict], content.get("service_target"))
    service = t.cast(str, service_target.get("service")) if service_target is not None else None
    env = t.cast(str, service_target.get("env")) if service_target is not None else None
    cluster_target = t.cast(t.Optional[dict], content.get("k8s_target_v2"))

    # Precedence ordering goes from most specific to least specific:
    # 1. Service, 2. Env, 3. Cluster target, 4. Wildcard `*`
    return (
        ((service is not None and service != "*") << 2)
        | ((env is not None and env != "*") << 1)
        | (cluster_target is not None) << 0
    )


class APMTracingAdapter(PubSub):
    __publisher_class__ = RemoteConfigPublisher
    __subscriber_class__ = RemoteConfigSubscriber
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self):
        self._publisher = self.__publisher_class__(self.__shared_data__)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, self.rc_callback, "APM_TRACING")

        # Configuration overrides
        self.config_map: t.Dict[str, Payload] = {}

    def get_chained_lib_config(self) -> t.ChainMap:
        # Get items in insertion order, then sort by precedence while preserving order for ties
        items_with_order = [(i, p) for i, p in enumerate(self.config_map.values())]

        return ChainMap(
            *(
                t.cast(dict, content["lib_config"])
                for content in (
                    p.content
                    for _, p in sorted(
                        items_with_order,
                        key=lambda x: (
                            config_key(x[1]),
                            x[0],
                        ),  # Higher precedence first, then newer (higher index) first
                        reverse=True,
                    )
                    if p.content is not None and "lib_config" in p.content
                )
            )
        )

    def rc_callback(self, payloads: t.Sequence[Payload]) -> None:
        seen_config_ids = set()
        for payload in payloads:
            if payload.metadata is None:
                log.debug("ignoring invalid APM Tracing remote config payload, path: %s", payload.path)
                continue

            log.debug("Received APM tracing config payload: %s", payload)

            config_id = payload.metadata.id
            seen_config_ids.add(config_id)

            if (content := payload.content) is None:
                log.debug(
                    "ignoring invalid APM Tracing remote config payload with no content, product: %s, path: %s",
                    payload.metadata.product_name,
                    payload.path,
                )
                continue

            service_target = t.cast(t.Optional[dict], content.get("service_target"))

            service = t.cast(str, service_target.get("service")) if service_target is not None else None
            env = t.cast(str, service_target.get("env")) if service_target is not None else None

            if service is not None and service != "*" and service != config.service:
                log.debug("ignoring APM Tracing remote config payload for service: %r != %r", service, config.service)
                continue

            if env is not None and env != "*" and env != config.env:
                log.debug("ignoring APM Tracing remote config payload for env: %r != %r", env, config.env)
                continue

            self.config_map[config_id] = payload

        # Remove configurations that are no longer present
        for config_id in set(self.config_map.keys()) - seen_config_ids:
            log.debug("Removing APM tracing config %s", config_id)
            self.config_map.pop(config_id, None)

        dispatch("apm-tracing.rc", (dict(self.get_chained_lib_config()), config))


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
