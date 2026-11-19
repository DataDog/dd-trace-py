"""Remote Config callback that reconfigures and restarts the profiler."""

from collections.abc import Sequence
import re
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.profiling import lifecycle
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.settings._config import config as global_config
from ddtrace.internal.settings.profiling import config as profiling_config
from ddtrace.internal.settings.profiling import exception_is_available
from ddtrace.internal.settings.profiling import stack_is_available


log = get_logger(__name__)

# All the profiling configurations delivered to the current tracer through Remote Config that target it.
# - The key is the Remote Config id of the configuration
# - The value is the payload content, with only its valid targets
# Between configurations of the same specificity, the one with the greatest id wins, so delivery order does not matter.
debug_configs: dict[str, dict[str, t.Any]] = {}

# Specificity levels of a target, from least to most specific:
#   other tags < env < service < workload < container name < pod < container ID.
# The infrastructure levels are recognized by the tag names the Datadog Agent assigns.
_LEVEL_OTHER_TAGS: t.Final[int] = 1
_LEVEL_ENV: t.Final[int] = 2
_LEVEL_SERVICE: t.Final[int] = 3
_LEVEL_BY_TAG_NAME: dict[str, int] = {
    **dict.fromkeys(
        (
            "kube_deployment",
            "kube_replica_set",
            "kube_stateful_set",
            "kube_daemon_set",
            "kube_job",
            "kube_cronjob",
            "kube_argo_rollout",
            "oshift_deployment",
            "task_family",
        ),
        4,
    ),
    **dict.fromkeys(("kube_container_name", "container_name", "ecs_container_name"), 5),
    **dict.fromkeys(("pod_name", "task_arn"), 6),
    "container_id": 7,
}
# A level outranks any number of conditions, which only break ties within a level.
_CONDITIONS_PER_LEVEL: t.Final[int] = 1000
_MAX_SERVICE_LENGTH: t.Final[int] = 100

# The Profiler arguments that remote config controls, and the dotted name of the profiling_config setting that enables
# each collector. With the settings in _SETTINGS, they are all a restart can change, so the running profiler is
# compared with the merged configuration on these only.
_COLLECTOR_ARGUMENTS: dict[str, str] = {
    "_stack_collector_enabled": "stack.enabled",
    "_memory_collector_enabled": "memory.enabled",
    "_lock_collector_enabled": "lock.enabled",
    "_exception_profiling_enabled": "exception.enabled",
}

# The settings remote config can change: the name in the payload, the dotted name in profiling_config, and the JSON
# type of the payload value. The Profiler and its collectors read each of them when they are built or started, so a
# restart applies the new value, and applies the value from the environment again once no configuration sets it.
_SETTINGS: dict[str, tuple[str, type]] = {
    # Note: this is currently imperfect, as existing Sample objects have a predefined value for max frames.
    # Changing the max stack depth will only affect new Samples.
    "maxStackDepth": ("max_frames", int),
    "pythonMemoryMemDomain": ("memory.mem_domain_enabled", bool),
}

# The settings of the schema that only apply to the tracers of other languages.
_OTHER_LANGUAGE_SETTINGS: frozenset[str] = frozenset(("rubySignalWorkaround",))

# The profiling_config variables by dotted name, to validate remote config values like the environment variables.
_SETTING_VARIABLES: dict[str, t.Any] = dict(type(profiling_config).items(recursive=True))


def _profilers_from_env() -> dict[str, bool]:
    """Return the remote config profilers that the environment enables."""
    enabled = bool(profiling_config.enabled)
    stack = enabled and bool(profiling_config.stack.enabled)
    memory = enabled and bool(profiling_config.memory.enabled)
    return {
        "cpuTime": stack,
        "wallTime": stack,
        "heapObjects": memory,
        "heapSize": memory,
        "allocations": memory,
        "exceptions": enabled and bool(profiling_config.exception.enabled),
        "locks": enabled and bool(profiling_config.lock.enabled),
    }


# _restart_profiler writes the collectors remote config wants in profiling_config, so the ones from the environment are
# read once, when this module is imported. profiling_config.parsed cannot be used instead: it holds the values from
# before ddtrace.internal.settings.profiling turned off the collectors that are not available.
_PROFILERS_FROM_ENV: dict[str, bool] = _profilers_from_env()


def _get_default_config() -> dict[str, t.Any]:
    """Return the configuration from the environment, which applies to the values no remote configuration sets."""
    return {"profilers": dict(_PROFILERS_FROM_ENV), "settings": _settings_from_env()}


def _setting_owner(path: str) -> tuple[t.Any, str]:
    """Return the profiling_config object that holds the setting at the dotted path, and the setting name in it."""
    *parents, name = path.split(".")
    owner: t.Any = profiling_config
    for parent in parents:
        owner = getattr(owner, parent)
    return owner, name


def _parse_setting(key: str, value: t.Any) -> t.Any:
    """Convert a payload setting to its profiling_config value. Raise TypeError or ValueError if it is invalid."""
    path, json_type = _SETTINGS[key]
    # bool is a subclass of int, but JSON booleans are not integers.
    if not isinstance(value, json_type) or (json_type is int and isinstance(value, bool)):
        raise TypeError(f"expected {json_type.__name__}, got {type(value).__name__}")

    variable = _SETTING_VARIABLES[path]
    parsed = variable.parser(str(value)) if variable.parser is not None else variable.type(value)
    if variable.validator is not None:
        variable.validator(parsed)

    return parsed


def _settings_from_env() -> dict[str, t.Any]:
    """Return the settings parsed from the environment, before remote config changed any of them."""
    settings: dict[str, t.Any] = {}
    for key, (path, _) in _SETTINGS.items():
        owner, name = _setting_owner(path)
        settings[key] = getattr(owner.parsed, name)

    return settings


def _applied_settings() -> dict[str, t.Any]:
    """Return the settings in profiling_config, which the running profiler was built with."""
    return {key: getattr(*_setting_owner(path)) for key, (path, _) in _SETTINGS.items()}


def _apply_settings(settings: dict[str, t.Any]) -> None:
    for key, value in settings.items():
        setattr(*_setting_owner(_SETTINGS[key][0]), value)


def _apply_collectors(wanted: dict[str, bool]) -> None:
    """Mark the wanted collectors enabled in profiling_config, and the others disabled.

    The profiler_config tag and the settings uploaded with each profile read the collectors from profiling_config, so
    they describe the profiler remote config started instead of the configuration from the environment.
    """
    # TODO: DD_PROFILING_HEAP_ENABLED=false sets heap.sample_size to 0, and nothing here changes it, so a memory
    # collector started for heapSize collects no heap profile. heapSize and allocations share that sample size.
    for argument, enabled in wanted.items():
        setattr(*_setting_owner(_COLLECTOR_ARGUMENTS[argument]), enabled)


def _normalize(name: t.Optional[str]) -> t.Optional[str]:
    """Normalize a service or env name like the Agent, which matches the targets against normalized values."""
    if not name:
        return None

    normalized = re.sub(r"^[^a-z0-9]+", "", re.sub(r"[^a-z0-9_:./-]", "_", name.lower()))
    return normalized[:_MAX_SERVICE_LENGTH]


def _is_valid_target(target: t.Any) -> bool:
    if not isinstance(target, dict):
        return False

    if any(target.get(key) is not None and not isinstance(target[key], str) for key in ("service", "env")):
        return False

    tags = target.get("tags")
    return tags is None or (isinstance(tags, list) and all(isinstance(tag, str) for tag in tags))


def _parse_target(target: dict[str, t.Any]) -> tuple[list[str], list[str], list[str]]:
    """Returns the targeted services and environments, and the names of the other tags of the target."""
    services: list[str] = [target["service"]] if target.get("service") is not None else []
    environments: list[str] = [target["env"]] if target.get("env") is not None else []
    tag_names: list[str] = []

    for tag in target.get("tags") or []:
        key, _, value = tag.partition(":")
        key = key.lower()
        if key == "service":
            services.append(value)
        elif key == "env":
            environments.append(value)
        else:
            tag_names.append(key)

    return services, environments, tag_names


def _target_matches(target: dict[str, t.Any]) -> bool:
    """Whether the target is consistent with the service and env of the current tracer."""
    services, environments, _ = _parse_target(target)
    service = _normalize(global_config.service)
    if any(s != "*" and _normalize(s) != service for s in services):
        return False

    # The Agent may target configs using an environment configured only at the Agent level.
    env = _normalize(global_config.env)
    if env and any(e != "*" and _normalize(e) != env for e in environments):
        return False

    return True


def _specificity(target: dict[str, t.Any]) -> int:
    """Return how specific the target is: a configuration with a higher value has precedence.

    A target ranks by its most specific condition (see _LEVEL_BY_TAG_NAME), then by its number of conditions.
    """
    services, environments, tag_names = _parse_target(target)
    service_conditions = sum(service != "*" for service in services)
    env_conditions = sum(env != "*" for env in environments)

    level = _LEVEL_SERVICE if service_conditions else _LEVEL_ENV if env_conditions else 0
    for name in tag_names:
        level = max(level, _LEVEL_BY_TAG_NAME.get(name, _LEVEL_OTHER_TAGS))

    return level * _CONDITIONS_PER_LEVEL + service_conditions + env_conditions + len(tag_names)


def _config_specificity(config: dict[str, t.Any]) -> int:
    """Return the specificity of the most specific target consistent with the current tracer, 0 if there is none.

    Remote Config only delivers the configurations that apply to the tracer, so the targets only rank them. The tag
    conditions are assumed to hold: the Agent matched them, and the tracer does not know all its tags.
    """
    return max((_specificity(target) for target in config.get("targets") or [] if _target_matches(target)), default=0)


class ProfilingRCCallback(RCCallback):
    def __call__(self, payloads: Sequence[Payload]) -> None:
        """Called by Remote Config client when it receives a batch of updated configurations.

        Note: Not every configuration that applies to the current tracer is sent in payloads, so the
        callback should have its own mechanism to track the unchanged configurations (that are not passed
        in payloads here).
        """
        log.debug("[profiling-remote-config] Received %d DEBUG RC payloads %s", len(payloads), payloads)

        changed = False
        for raw_payload in payloads:
            config_id = raw_payload.metadata.id
            content: t.Any = raw_payload.content

            # A removed configuration has no content. An updated configuration can also become invalid.
            if debug_configs.pop(config_id, None) is not None:
                changed = True
                log.debug("[profiling-remote-config] Removed the previous version of config %s", config_id)

            if content is None:
                continue

            if not isinstance(content, dict):
                log.warning("[profiling-remote-config] Skipping invalid payload %s", content)
                continue

            if "profilers" not in content and "settings" not in content:
                log.debug("[profiling-remote-config] Skipping non-profiling payload %s", content)
                continue

            # A payload without targets targets all the tracers.
            all_targets: t.Union[list[t.Any], t.Any] = content.get("targets")
            if all_targets is None:
                all_targets = []
            if not isinstance(all_targets, list):
                log.warning("[profiling-remote-config] Skipping payload with invalid targets %s", content)
                continue

            valid_targets: list[dict[str, t.Any]] = []
            for target in all_targets:
                if not _is_valid_target(target):
                    log.warning("[profiling-remote-config] Skipping invalid target %s", target)
                    continue

                valid_targets.append(target)

            # A list of only invalid targets is rejected rather than treated as untargeted.
            if all_targets and not valid_targets:
                log.warning("[profiling-remote-config] Skipping payload with no valid targets %s", content)
                continue

            debug_configs[config_id] = {**content, "targets": valid_targets}
            changed = True

        # A profiler started by importing ddtrace.profiling.auto can run without matching the configuration from the
        # environment, so reconciling on other payloads would stop it.
        if not changed:
            log.debug("[profiling-remote-config] No changes in the Profiling-related DEBUG RC configs, skipping.")
            return

        if debug_configs:
            log.debug("[profiling-remote-config] Profiling-related DEBUG RC configs: %s", debug_configs)
        else:
            log.debug(
                "[profiling-remote-config] No Profiling-related DEBUG RC configs, falling back to default settings."
            )

        with lifecycle.lock:
            self.handle_profiling_configs([debug_configs[config_id] for config_id in sorted(debug_configs)])

    def handle_profiling_configs(self, configs: list[dict[str, t.Any]]) -> None:
        """Restart the Profiler if the merged configurations differ from its collectors or the applied settings."""
        merged_config = _merge_configs(configs)
        wanted = _wanted_collectors(merged_config["profilers"])
        settings: dict[str, t.Any] = merged_config["settings"]
        if wanted == _running_collectors() and settings == _applied_settings():
            log.debug("[profiling-remote-config] The running Profiler already has the wanted configuration, skipping.")
            return

        log.debug("[profiling-remote-config] Restarting the Profiler with the new settings: %s", merged_config)
        _restart_profiler(wanted, settings)


def _merge_configs(configs: list[dict[str, t.Any]]) -> dict[str, t.Any]:
    """Merge all the Remote Config files that were sent to the Tracer over the configuration from the environment."""
    merged_config: dict[str, t.Any] = _get_default_config()

    # Configurations are merged from "least specific scope" to "most specific scope" (see _specificity). Between
    # configurations of the same specificity, the last one in configs wins.
    # - A target ranks by its most specific condition, then by its number of conditions: service + env is more
    #   specific than service alone, and kube_cluster_name + service too.
    # - The service and env tags count as the service and env fields.
    # - A target that is not consistent with the service and env of the tracer does not rank the configuration.
    #
    # Precedence is defined as follows (from least to most specific):
    # - tags (except for tags mentioned elsewhere in the order)
    # - environment and env tag
    # - service and service tag
    # - workload (e.g. kube_deployment, task_family)
    # - container name (e.g. kube_container_name)
    # - pod (pod_name, task_arn)
    # - container_id
    #
    # For profilers:
    # - If a profiler is enabled in a more specific configuration, it overrides the previous configuration.
    # - If a profiler is disabled in a more specific configuration, it overrides the previous configuration.
    # - If a profiler is unset (field omitted) in a more specific configuration, it is a no-op.
    # For settings:
    # - If a setting is set in a more specific configuration, it overrides the previous configuration.
    # - If a setting is unset (field omitted) in a more specific configuration, it is a no-op.
    # A configuration with more than one target has the specificity of its most specific target.
    ordered = sorted(enumerate(configs), key=lambda ic: (_config_specificity(ic[1]), ic[0]))
    for _, config in ordered:
        profilers = config.get("profilers") or {}
        if not isinstance(profilers, dict):
            log.warning("[profiling-remote-config] Skipping non-profiling payload %s", config)
            continue

        for name, enabled in profilers.items():
            if isinstance(enabled, bool):
                merged_config["profilers"][name] = enabled

        settings = config.get("settings") or {}
        if not isinstance(settings, dict):
            log.warning("[profiling-remote-config] Skipping non-profiling payload %s", config)
            continue

        for name, value in settings.items():
            if value is None or name in _OTHER_LANGUAGE_SETTINGS:
                continue

            if name not in _SETTINGS:
                log.warning("[profiling-remote-config] Ignoring unknown setting %s", name)
                continue

            # An invalid setting is ignored like an unset one, so the less specific configurations still apply.
            try:
                merged_config["settings"][name] = _parse_setting(name, value)
            except (TypeError, ValueError, OverflowError) as e:
                log.warning("[profiling-remote-config] Ignoring setting %s with invalid value %r: %s", name, value, e)

    return merged_config


def _wanted_collectors(profilers: dict[str, bool]) -> dict[str, bool]:
    """Map the remote config profilers to the Profiler arguments in _COLLECTOR_ARGUMENTS.

    The stack collector produces both CPU and wall time profiles, and the memory collector the heap live objects, heap
    live size and allocation profiles, so the profiles of a collector can only be enabled together.

    Unavailable collectors are ignored.
    """
    return {
        "_stack_collector_enabled": stack_is_available and bool(profilers.get("cpuTime") or profilers.get("wallTime")),
        "_memory_collector_enabled": bool(
            profilers.get("heapObjects") or profilers.get("heapSize") or profilers.get("allocations")
        ),
        "_lock_collector_enabled": bool(profilers.get("locks")),
        "_exception_profiling_enabled": exception_is_available and bool(profilers.get("exceptions")),
    }


def _running_collectors() -> dict[str, bool]:
    """Return the Profiler arguments of the running bootstrap profiler, all False if none is running."""
    current = lifecycle.current_profiler()
    if current is None or current._profiler.status != ServiceStatus.RUNNING:
        return dict.fromkeys(_COLLECTOR_ARGUMENTS, False)

    return {name: bool(getattr(current._profiler, name)) for name in _COLLECTOR_ARGUMENTS}


def _restart_profiler(wanted: dict[str, bool], settings: dict[str, t.Any]) -> None:
    """Replace the bootstrap profiler with one built from wanted and settings.

    On failure no bootstrap profiler runs, which _running_collectors reports, so the next change retries.
    The caller must hold lifecycle.lock.
    """
    # TODO: This runs in the remote config thread, and stopping the profiler uploads the last profile
    #   synchronously. That delays the other remote config products, and forks too, since the before-fork
    #   hook joins the remote config thread. Move the restart off the remote config thread.
    lifecycle.stop_profiler()

    # Applied even when no profiler starts, so that a profiler started later uses the settings too, and profiling_config
    # does not report collectors that no longer run.
    _apply_settings(settings)
    _apply_collectors(wanted)

    # This will ignore the PyTorch profiler being enabled locally on its own. It's an edge case
    # that I'm OK with for the time being.
    # Note: this does not check for Native Heap, but Native Heap is never uninstalled/stopped,
    #   so it does not need to be checked here.
    if not any(wanted.values()):
        return

    from ddtrace.profiling.profiler import Profiler

    active: t.Optional[Profiler] = Profiler._active_instance
    if active is not None and active._profiler.status == ServiceStatus.RUNNING:
        log.warning("[profiling-remote-config] A profiler not owned by remote config is running, not starting one.")
        return

    try:
        new_profiler = Profiler(**wanted)
        new_profiler.start()
    except Exception:
        log.exception("[profiling-remote-config] Failed to start the Profiler")
        return

    # Profiler.start returns without an error when another profiler won the race to become active.
    if new_profiler._profiler.status != ServiceStatus.RUNNING:
        log.warning("[profiling-remote-config] The Profiler did not start.")
        return

    lifecycle.set_profiler(new_profiler)
