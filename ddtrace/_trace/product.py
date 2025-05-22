import enum
import os
import typing as t

from envier import En

from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.settings._config import Config
from ddtrace.settings._config import config
from ddtrace.settings.http import HttpConfig


requires = ["remote-configuration"]


class _Config(En):
    __prefix__ = "dd.trace"

    enabled = En.v(bool, "enabled", default=True)
    global_tags = En.v(dict, "global_tags", parser=parse_tags_str, default={})


_config = _Config()


# TODO: Consolidate with the apm_tracing_rc function
def _on_global_config_update(cfg: Config, items: t.List[str]) -> None:
    from ddtrace import tracer

    # sampling configs always come as a pair
    if "_trace_sampling_rules" in items:
        tracer._sampler.set_sampling_rules(cfg._trace_sampling_rules)

    if "tags" in items:
        tracer._tags = cfg.tags.copy()

    if "_tracing_enabled" in items:
        if tracer.enabled:
            if cfg._tracing_enabled is False:
                tracer.enabled = False
        else:
            # the product specification says not to allow tracing to be re-enabled remotely at runtime
            if cfg._tracing_enabled is True and cfg._get_source("_tracing_enabled") != "remote_config":
                tracer.enabled = True

    if "_logs_injection" in items:
        # TODO: Refactor the logs injection code to import from a core component
        if config._logs_injection:
            from ddtrace.contrib.internal.logging.patch import patch

            patch()
        else:
            from ddtrace.contrib.internal.logging.patch import unpatch

            unpatch()


def post_preload():
    if _config.enabled:
        from ddtrace._monkey import _patch_all

        modules_to_patch = os.getenv("DD_PATCH_MODULES")
        modules_to_str = parse_tags_str(modules_to_patch)
        modules_to_bool = {k: asbool(v) for k, v in modules_to_str.items()}
        _patch_all(**modules_to_bool)


def start():
    if _config.enabled:
        apm_tracing_rc_subscribe(config)
        if config._trace_methods:
            from ddtrace.internal.tracemethods import _install_trace_methods

            _install_trace_methods(config._trace_methods)

    if _config.global_tags:
        from ddtrace.trace import tracer

        tracer.set_tags(_config.global_tags)


def restart(join=False):
    from ddtrace.trace import tracer

    if tracer.enabled:
        tracer._child_after_fork()


def stop(join=False):
    from ddtrace.trace import tracer

    if tracer.enabled:
        tracer.shutdown()


def at_exit(join=False):
    # at_exit hooks are currently registered when the tracer is created. This is
    # required to support non-global tracers (ex: CiVisibility and the Dummy Tracers used in tests).
    # TODO: Move the at_exit hooks from ddtrace.trace.Tracer._init__(....) to the product protocol,
    pass


class APMCapabilities(enum.IntFlag):
    APM_TRACING_SAMPLE_RATE = 1 << 12
    APM_TRACING_LOGS_INJECTION = 1 << 13
    APM_TRACING_HTTP_HEADER_TAGS = 1 << 14
    APM_TRACING_CUSTOM_TAGS = 1 << 15
    APM_TRACING_ENABLED = 1 << 19
    APM_TRACING_SAMPLE_RULES = 1 << 29


def apm_tracing_rc_subscribe(dd_config):
    dd_config._subscribe(
        ["_trace_sampling_rules", "_logs_injection", "tags", "_tracing_enabled"], _on_global_config_update
    )


def apm_tracing_rc(lib_config, dd_config):
    base_rc_config: t.Dict[str, t.Any] = {n: None for n in dd_config._config}

    if "tracing_sampling_rules" in lib_config or "tracing_sampling_rate" in lib_config:
        global_sampling_rate = lib_config.get("tracing_sampling_rate")
        trace_sampling_rules = lib_config.get("tracing_sampling_rules") or []
        # returns None if no rules
        trace_sampling_rules = dd_config._convert_rc_trace_sampling_rules(trace_sampling_rules, global_sampling_rate)
        if trace_sampling_rules:
            base_rc_config["_trace_sampling_rules"] = trace_sampling_rules

    if "log_injection_enabled" in lib_config:
        base_rc_config["_logs_injection"] = lib_config["log_injection_enabled"]

    if "tracing_tags" in lib_config:
        tags = lib_config["tracing_tags"]
        if tags:
            tags = dd_config._format_tags(lib_config["tracing_tags"])
        base_rc_config["tags"] = tags

    if "tracing_enabled" in lib_config and lib_config["tracing_enabled"] is not None:
        base_rc_config["_tracing_enabled"] = asbool(lib_config["tracing_enabled"])

    if "tracing_header_tags" in lib_config:
        tags = lib_config["tracing_header_tags"]
        if tags:
            tags = dd_config._format_tags(lib_config["tracing_header_tags"])
        base_rc_config["_trace_http_header_tags"] = tags

    dd_config._set_config_items([(k, v, "remote_config") for k, v in base_rc_config.items()])

    # unconditionally handle the case where header tags have been unset
    header_tags_conf = dd_config._config["_trace_http_header_tags"]
    env_headers = header_tags_conf._env_value or {}
    code_headers = header_tags_conf._code_value or {}
    non_rc_header_tags = {**code_headers, **env_headers}
    selected_header_tags = base_rc_config.get("_trace_http_header_tags") or non_rc_header_tags
    dd_config._http = HttpConfig(header_tags=selected_header_tags)
