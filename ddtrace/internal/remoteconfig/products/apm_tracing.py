from collections import ChainMap
import enum
import typing as t

from ddtrace import config
from ddtrace.internal.core.event_hub import dispatch
from ddtrace.internal.core.event_hub import on
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import RCCallback


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


class APMTracingCallback(RCCallback):
    """Remote config callback for APM tracing configuration."""

    def __init__(self) -> None:
        """Initialize the APM tracing callback with empty configuration state."""
        self._config_map: t.Dict[str, Payload] = {}

    def _get_chained_lib_config(self) -> t.ChainMap:
        """Get merged library configuration from all configs, ordered by precedence."""
        # Get items in insertion order, then sort by precedence while preserving order for ties
        items_with_order = [(i, p) for i, p in enumerate(self._config_map.values())]

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

    def __call__(self, payloads: t.Sequence[Payload]) -> None:
        """Process APM tracing configuration payloads.

        Args:
            payloads: Sequence of configuration payloads to process
        """
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

            self._config_map[config_id] = payload

        # Remove configurations that are no longer present
        for config_id in set(self._config_map.keys()) - seen_config_ids:
            log.debug("Removing APM tracing config %s", config_id)
            self._config_map.pop(config_id, None)

        dispatch("apm-tracing.rc", (dict(self._get_chained_lib_config()), config))


def post_preload():
    pass


def start():
    if config._remote_config_enabled:
        from ddtrace.internal.products import manager
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_poller.register(
            "APM_TRACING",
            APMTracingCallback(),
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
