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

    @staticmethod
    def _get_chained_lib_config(config_map: t.Mapping[str, Payload]) -> t.ChainMap:
        """Get merged library configuration from all configs, ordered by precedence."""
        # Higher config_key first; on ties, higher insertion index (newer) first.
        ordered = sorted(
            enumerate(config_map.values()),
            key=lambda ip: (config_key(ip[1]), ip[0]),
            reverse=True,
        )
        lib_configs = [
            t.cast(dict, p.content["lib_config"])
            for _, p in ordered
            if p.content is not None and "lib_config" in p.content
        ]
        return ChainMap(*lib_configs)

    def _process_payloads(self, payloads: t.Sequence[Payload]) -> t.ChainMap:
        config_map: dict[str, Payload] = {}
        for payload in payloads:
            if payload.metadata is None:
                log.debug("ignoring invalid APM Tracing remote config payload, path: %s", payload.path)
                continue

            log.debug("Received APM tracing config payload: %s", payload)

            if payload.content is None:
                if payload.metadata.id in config_map:
                    log.debug("Deleting config %s", payload.metadata.id)
                    config_map.pop(payload.metadata.id)
                continue

            service_target = t.cast(t.Optional[dict], payload.content.get("service_target"))
            service = t.cast(str, service_target.get("service")) if service_target is not None else None
            env = t.cast(str, service_target.get("env")) if service_target is not None else None

            if service is not None and service != "*" and service != config.service:
                log.debug("ignoring APM Tracing remote config payload for service: %r != %r", service, config.service)
                continue

            if env is not None and env != "*" and env != config.env:
                log.debug("ignoring APM Tracing remote config payload for env: %r != %r", env, config.env)
                continue

            config_map[payload.metadata.id] = payload
        return self._get_chained_lib_config(config_map)

    def __call__(self, payloads: t.Sequence[Payload]) -> None:
        """Process APM tracing configuration payloads.

        Args:
            payloads: Sequence of configuration payloads to process
        """
        chain = self._process_payloads(payloads)
        dispatch("apm-tracing.rc", (dict(chain), config))


def post_preload():
    pass


def enabled() -> bool:
    return config._remote_config_enabled


def start():
    from ddtrace.internal.products import manager
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    remoteconfig_poller.register_callback(
        "APM_TRACING",
        APMTracingCallback(),
        capabilities=[
            cap for product in manager.__products__.values() for cap in getattr(product, "APMCapabilities", [])
        ],
    )
    remoteconfig_poller.enable_product("APM_TRACING")

    # Register remote config handlers
    for name, product in manager.__products__.items():
        if (rc_handler := getattr(product, "apm_tracing_rc", None)) is not None:
            on("apm-tracing.rc", rc_handler, name)


def restart(join=False):
    pass


def stop(join=False):
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    remoteconfig_poller.unregister_callback("APM_TRACING")
    remoteconfig_poller.disable_product("APM_TRACING")
