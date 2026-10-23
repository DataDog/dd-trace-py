"""Product plugin that lets the profiler react to DEBUG remote config payloads."""

from collections.abc import Sequence
import logging
import typing as t

from ddtrace.internal import _unpatched
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import RemoteConfigProduct
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.settings._config import config as global_config
from ddtrace.internal.settings.profiling import config as profiling_config


log = get_logger(__name__)
_handler: "logging.StreamHandler[t.TextIO]" = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
log.addHandler(_handler)
log.propagate = False

requires: list[str] = ["remote-configuration"]

# All the profiling configurations delivered to the current tracer through Remote Config that target it.
# - The key is the Remote Config id of the configuration
# - The value is the payload content
# An updated configuration moves to the end, so it wins over the configurations of the same specificity.
debug_configs: dict[str, dict[str, t.Any]] = {}

# Tags that scope a configuration to a single instance, the most specific scope.
_INSTANCE_TAG_KEYS: frozenset[str] = frozenset(("pod_name", "container_id"))

previous_profiling_remote_configuration: t.Optional[dict[str, t.Any]] = None

# This Lock is used to serialise Profiler object swaps.
# We don't need a forksafe lock, because forks can never interrupt a swap. The before-fork hook in
# ddtrace.internal.threads joins every PeriodicThread, including the remote config poller and
# subscriber, and a PeriodicThread only stops between two callbacks. So there is no need to hold
# this lock across forks.
# uWSGI forks workers in C without running the before-fork hooks; that fork can happen mid-swap,
# which is why restart replaces the lock in the child.
_lock: _unpatched.threading_Lock = _unpatched.threading_Lock()


def _get_default_config() -> dict[str, t.Any]:
    """Return the configuration from the environment, which applies to the values no remote configuration sets."""
    enabled = bool(profiling_config.enabled)
    stack = enabled and bool(profiling_config.stack.enabled)
    memory = enabled and bool(profiling_config.memory.enabled)
    return {
        "profilers": {
            "cpu": stack,
            "wallTime": stack,
            "heapSize": memory,
            "allocations": memory,
            "exception": enabled and bool(profiling_config.exception.enabled),
            "lock": enabled and bool(profiling_config.lock.enabled),
        },
        "settings": {},
    }


def _parse_target(target: dict[str, t.Any]) -> tuple[list[str], list[str], bool, bool]:
    """Returns the targeted services and environments, and whether the target has instance tags and other tags."""
    services: list[str] = [target["service"]] if target.get("service") is not None else []
    environments: list[str] = [target["env"]] if target.get("env") is not None else []
    has_instance_tags = False
    has_other_tags = False
    for tag in target.get("tags") or []:
        key, _, value = tag.partition(":")
        if key == "service":
            services.append(value)
        elif key == "env":
            environments.append(value)
        elif key in _INSTANCE_TAG_KEYS:
            has_instance_tags = True
        else:
            has_other_tags = True
    return services, environments, has_instance_tags, has_other_tags


def _target_matches(target: dict[str, t.Any]) -> bool:
    """Whether the target matches the service and env of the current tracer."""
    services, environments, _, _ = _parse_target(target)
    if any(service != "*" and service != global_config.service for service in services):
        return False

    # The Agent may target configs using an environment configured only at the Agent level.
    if global_config.env and any(env != "*" and env != global_config.env for env in environments):
        return False

    return True


def _specificity(target: dict[str, t.Any]) -> int:
    """Return how specific the target is: a configuration with a higher value has precedence.

    Each scope has its own bit, from least to most specific: env, other tags, service, instance (pod_name or
    container_id). A scope wins over all the less specific scopes together, and a combination of scopes wins over
    each of its scopes alone (e.g. service + env wins over service).
    """
    services, environments, has_instance_tags, has_other_tags = _parse_target(target)
    return (
        (has_instance_tags << 3)
        | (any(service != "*" for service in services) << 2)
        | (has_other_tags << 1)
        | (any(env != "*" for env in environments) << 0)
    )


class ProfilingRCCallback(RCCallback):
    def __call__(self, payloads: Sequence[Payload]) -> None:
        log.debug("[profiling-remote-config] Received %d DEBUG RC payloads %s", len(payloads), payloads)

        for raw_payload in payloads:
            config_id = raw_payload.metadata.id
            content = raw_payload.content

            # A removed configuration has no content. An updated configuration can also stop targeting this tracer.
            if debug_configs.pop(config_id, None) is not None:
                log.debug("[profiling-remote-config] Removed the previous version of config %s", config_id)

            if content is None:
                continue

            if "profilers" not in content:
                log.debug("[profiling-remote-config] Skipping non-profiling payload %s", content)
                continue

            target: dict[str, t.Any] = content.get("target") or {}
            if not _target_matches(target):
                log.debug("[profiling-remote-config] Skipping payload with target %s", target)
                continue

            debug_configs[config_id] = content

            # TODO: how do we detect when a certain config stops being distributed to us?
            #   like when the config file is deleted???

        if debug_configs:
            log.debug("[profiling-remote-config] Profiling-related DEBUG RC configs: %s", debug_configs)
        else:
            log.debug(
                "[profiling-remote-config] No Profiling-related DEBUG RC configs, falling back to default settings."
            )

        with _lock:
            self.handle_profiling_configs(list(debug_configs.values()))

    def handle_profiling_configs(self, configs: list[dict[str, t.Any]]) -> None:
        """Merges all the Remote Config files that were sent to the Tracer."""
        global previous_profiling_remote_configuration

        merged_config: dict[str, t.Any] = _get_default_config()

        # Configurations are merged from "least specific scope" to "most specific scope" (see _specificity). Between
        # configurations of the same specificity, the last one in configs wins.
        # - If a config is scoped to e.g. service + env, it is more specific than service or env alone.
        # - The service and env tags count as the service and env fields.
        # - If a config is scoped to e.g. kube_cluster_name + service, it is more specific than service alone.
        #
        # Precedence is defined as follows (from least to most specific):
        # - environment and env tag
        # - tags (except for tags mentioned elsewhere in the order)
        # - service and service tag
        # - pod_name / container_id
        #
        # For profilers:
        # - If a profiler is enabled in a more specific configuration, it overrides the previous configuration.
        # - If a profiler is disabled in a more specific configuration, it overrides the previous configuration.
        # - If a profiler is unset (field omitted) in a more specific configuration, it is a no-op.
        # For settings:
        # - If a setting is set in a more specific configuration, it overrides the previous configuration.
        # - If a setting is unset (field omitted) in a more specific configuration, it is a no-op.
        ordered = sorted(enumerate(configs), key=lambda ic: (_specificity(ic[1].get("target") or {}), ic[0]))
        for _, config in ordered:
            for name, enabled in (config.get("profilers") or {}).items():
                if isinstance(enabled, bool):
                    merged_config["profilers"][name] = enabled
            for name, value in (config.get("settings") or {}).items():
                if value is not None:
                    merged_config["settings"][name] = value

        # Before the first remote configuration applies, the profiler runs with the configuration from the environment.
        previous_config: dict[str, t.Any] = (
            previous_profiling_remote_configuration
            if previous_profiling_remote_configuration is not None
            else _get_default_config()
        )
        if previous_config == merged_config:
            log.debug(
                "[profiling-remote-config] No effective changes in the Profiling-related DEBUG RC configs, skipping."
            )
            return

        # Stop the Profiler and restart it
        log.debug("[profiling-remote-config] Restarting the Profiler with the new settings: %s", merged_config)
        if _restart_profiler(merged_config["profilers"]):
            previous_profiling_remote_configuration = merged_config


def _restart_profiler(profilers: dict[str, bool]) -> bool:
    """Replace the bootstrap profiler. Return False if the new profiler did not start, so the next payload retries."""
    from ddtrace.profiling import bootstrap
    from ddtrace.profiling.profiler import Profiler

    # The stack collector produces both CPU and wall time profiles, and the memory collector both heap and allocation
    # profiles, so each pair can only be enabled together.
    stack_enabled = bool(profilers.get("cpu") or profilers.get("wallTime"))
    memory_enabled = bool(profilers.get("heapSize") or profilers.get("allocations"))
    lock_enabled = bool(profilers.get("lock"))
    exception_enabled = bool(profilers.get("exception"))

    current: t.Optional[Profiler] = getattr(bootstrap, "profiler", None)
    if current is not None:
        current.stop(flush=True)
        del bootstrap.profiler

    if not (stack_enabled or memory_enabled or lock_enabled or exception_enabled):
        return True

    active: t.Optional[Profiler] = Profiler._active_instance
    if active is not None and active._profiler.status == ServiceStatus.RUNNING:
        log.warning("[profiling-remote-config] A profiler not owned by remote config is running, not starting one.")
        return False

    try:
        new_profiler = Profiler(
            _stack_collector_enabled=stack_enabled,
            _memory_collector_enabled=memory_enabled,
            _lock_collector_enabled=lock_enabled,
            _exception_profiling_enabled=exception_enabled,
        )
        new_profiler.start()
    except Exception:
        log.exception("[profiling-remote-config] Failed to start the Profiler")
        return False

    # Profiler.start returns without an error when another profiler won the race to become active.
    if new_profiler._profiler.status != ServiceStatus.RUNNING:
        log.warning("[profiling-remote-config] The Profiler did not start.")
        return False

    bootstrap.profiler = new_profiler
    return True


def post_preload() -> None:
    pass


def enabled() -> bool:
    return bool(global_config._remote_config_enabled)


def start() -> None:
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    remoteconfig_poller.register_callback(RemoteConfigProduct.Debug, ProfilingRCCallback())
    remoteconfig_poller.enable_product(RemoteConfigProduct.Debug)


def restart(join: bool = False) -> None:
    # A uWSGI fork can happen while the remote config thread holds the lock; that thread does not exist in the child.
    global _lock

    _lock = _unpatched.threading_Lock()


def stop(join: bool = False) -> None:
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    remoteconfig_poller.unregister_callback(RemoteConfigProduct.Debug)
    remoteconfig_poller.disable_product(RemoteConfigProduct.Debug)
