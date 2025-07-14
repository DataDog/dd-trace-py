import enum
import json
import os
import typing as t

from envier import En

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.settings._config import Config
from ddtrace.settings._config import config
from ddtrace.settings.http import HttpConfig
from ddtrace.vendor.debtcollector import deprecate


log = get_logger(__name__)

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
        log.debug("Updated tracer sampling rules via remote_config: %s", cfg._trace_sampling_rules)

    if "tags" in items:
        tracer._tags = cfg.tags.copy()
        log.debug("Updated tracer tags via remote_config: %s", tracer._tags)

    if "_tracing_enabled" in items:
        if tracer.enabled and cfg._tracing_enabled is False:
            tracer.enabled = False
            log.debug("Tracing disabled via remote_config. Config Items: %s", items)
        elif cfg._tracing_enabled is True and cfg._get_source("_tracing_enabled") != "remote_config":
            tracer.enabled = True
            log.debug("Tracing enabled via remote_config. Config Items: %s", items)

    if "_trace_http_header_tags" in items:
        # Update the HTTP header tags configuration
        cfg._http = HttpConfig(header_tags=cfg._trace_http_header_tags)
        log.debug("Updated HTTP header tags configuration via remote_config: %s", cfg._http._header_tags)

    if "_logs_injection" in items:
        # Update the logs injection configuration
        log.debug("Updated logs injection configuration via remote_config: %s", cfg._logs_injection)


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

        # ddtrace library supports setting tracer tags using both DD_TRACE_GLOBAL_TAGS and DD_TAGS
        # moving forward we should only support DD_TRACE_GLOBAL_TAGS.
        # TODO(munir): Set dd_tags here
        deprecate(
            "DD_TRACE_GLOBAL_TAGS is deprecated",
            message="Please migrate to using DD_TAGS instead",
            category=DDTraceDeprecationWarning,
            removal_version="4.0.0",
        )
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
        ["_trace_sampling_rules", "_logs_injection", "tags", "_tracing_enabled", "_trace_http_header_tags"],
        _on_global_config_update,
    )
    log.debug(
        "APM Tracing Remote Config enabled for trace sampling rules, log injection, dd tags, "
        "tracing enablement, and HTTP header tags.\nConfigs on startup: sampling_rules: %s, "
        "logs_injection: %s, tags: %s, tracing_enabled: %s, trace_http_header_tags: %s",
        dd_config._trace_sampling_rules,
        dd_config._logs_injection,
        dd_config.tags,
        dd_config._tracing_enabled,
        dd_config._trace_http_header_tags,
    )


def _remove_invalid_rules(rc_rules: t.List) -> t.List:
    """Remove invalid sampling rules from the given list"""
    # loop through list of dictionaries, if a dictionary doesn't have certain attributes, remove it
    new_rc_rules = []
    for rule in rc_rules:
        if (
            ("service" not in rule and "name" not in rule and "resource" not in rule and "tags" not in rule)
            or "sample_rate" not in rule
            or "provenance" not in rule
        ):
            log.debug("Invalid sampling rule from remoteconfig found in: %s, rule will be removed: %s", rc_rules, rule)
            continue
        new_rc_rules.append(rule)

    return new_rc_rules


def _convert_rc_trace_sampling_rules(
    rc_rules: t.List[t.Dict[str, t.Any]], global_sample_rate: t.Optional[float]
) -> t.Optional[str]:
    """Example of an incoming rule:
    [
      {
        "service": "my-service",
        "name": "web.request",
        "resource": "*",
        "provenance": "customer",
        "sample_rate": 1.0,
        "tags": [
          {
            "key": "care_about",
            "value_glob": "yes"
          },
          {
            "key": "region",
            "value_glob": "us-*"
          }
        ]
      }
    ]

            Example of a converted rule:
            '[{"sample_rate":1.0,"service":"my-service","resource":"*","name":"web.request","tags":{"care_about":"yes","region":"us-*"},provenance":"customer"}]'
    """
    rc_rules = _remove_invalid_rules(rc_rules)
    for rule in rc_rules:
        if "tags" in rule:
            # Remote config provides sampling rule tags as a list,
            # but DD_TRACE_SAMPLING_RULES expects them as a dict.
            # Here we convert tags to a dict to ensure a consistent format.
            if isinstance(rule["tags"], list):
                rule["tags"] = {tag["key"]: tag["value_glob"] for tag in rule["tags"]}

    if global_sample_rate is not None:
        rc_rules.append({"sample_rate": global_sample_rate})

    if rc_rules:
        return json.dumps(rc_rules)
    else:
        return None


def apm_tracing_rc(lib_config, dd_config):
    new_rc_configs: t.Dict[str, t.Any] = {n: None for n in dd_config._config}

    if "tracing_sampling_rules" in lib_config or "tracing_sampling_rate" in lib_config:
        global_sampling_rate = lib_config.get("tracing_sampling_rate")
        trace_sampling_rules = lib_config.get("tracing_sampling_rules") or []
        # returns None if no rules
        trace_sampling_rules = _convert_rc_trace_sampling_rules(trace_sampling_rules, global_sampling_rate)
        if trace_sampling_rules:
            new_rc_configs["_trace_sampling_rules"] = trace_sampling_rules

    if "log_injection_enabled" in lib_config:
        new_rc_configs["_logs_injection"] = str(lib_config["log_injection_enabled"]).lower()

    if "tracing_tags" in lib_config:
        tags = lib_config["tracing_tags"]
        if tags:
            tags = dd_config._format_tags(lib_config["tracing_tags"])
        new_rc_configs["tags"] = tags

    if "tracing_enabled" in lib_config and lib_config["tracing_enabled"] is not None:
        new_rc_configs["_tracing_enabled"] = asbool(lib_config["tracing_enabled"])

    if "tracing_header_tags" in lib_config:
        tags = lib_config["tracing_header_tags"]
        if tags:
            tags = dd_config._format_tags(lib_config["tracing_header_tags"])
        new_rc_configs["_trace_http_header_tags"] = tags

    dd_config._set_config_items([(k, v, "remote_config") for k, v in new_rc_configs.items()])
    log.debug("APM Tracing Received: %s from the Agent", lib_config)
