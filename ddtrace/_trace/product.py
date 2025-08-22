import enum
import json
import os
import typing as t

from envier import En

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.settings.http import HttpConfig
from ddtrace.vendor.debtcollector import deprecate


log = get_logger(__name__)

requires = ["remote-configuration"]


class _Config(En):
    __prefix__ = "dd.trace"

    enabled = En.v(bool, "enabled", default=True)
    global_tags = En.v(dict, "global_tags", parser=parse_tags_str, default={})


_config = _Config()


def post_preload():
    if _config.enabled:
        from ddtrace._monkey import _patch_all

        modules_to_patch = os.getenv("DD_PATCH_MODULES")
        modules_to_str = parse_tags_str(modules_to_patch)
        modules_to_bool = {k: asbool(v) for k, v in modules_to_str.items()}
        _patch_all(**modules_to_bool)


def start():
    if _config.enabled:
        from ddtrace.settings._config import config

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


def _convert_rc_trace_sampling_rules(lib_config) -> t.Optional[str]:
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
    if "tracing_sampling_rules" not in lib_config and "tracing_sampling_rate" not in lib_config:
        return None

    global_sample_rate = lib_config.get("tracing_sampling_rate")
    rc_rules = lib_config.get("tracing_sampling_rules") or []
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


def _convert_rc_tags(lib_config, key, dd_config):
    """Extract and format tags from remote config."""
    tags = lib_config.get(key)
    if not tags:
        return None

    if isinstance(tags[0], dict):
        pairs = [(item["header"], item["tag_name"]) for item in tags]
    else:
        pairs = [t.split(":") for t in tags]
    return {k: v for k, v in pairs}


def _convert_optional_bool(lib_config, key):
    if lib_config.get(key) is None:
        return None
    return asbool(lib_config[key])


def _apply_config_change(config_name, config_value, dd_config):
    """Apply configuration change and log the update."""
    from ddtrace import tracer

    if config_name == "_trace_sampling_rules":
        tracer._sampler.set_sampling_rules(config_value)
        log.debug("Updated tracer sampling rules via remote_config: %s", config_value)
    elif config_name == "tags":
        tracer._tags = (config_value or {}).copy()
        log.debug("Updated tracer tags via remote_config: %s", tracer._tags)
    elif config_name == "_tracing_enabled":
        if tracer.enabled and config_value is False:
            tracer.enabled = False
            log.debug("Tracing disabled via remote_config. Config: %s Value: %s", config_name, config_value)
        elif config_value is True and dd_config._config["_tracing_enabled"].source() != "remote_config":
            tracer.enabled = True
            log.debug("Tracing enabled via remote_config. Config: %s Value: %s", config_name, config_value)
    elif config_name == "_trace_http_header_tags":
        dd_config._http = HttpConfig(header_tags=config_value)
        log.debug("Updated HTTP header tags configuration via remote_config: %s", config_value)
    elif config_name == "_logs_injection":
        log.debug("Updated logs injection configuration via remote_config: %s", config_value)
    else:
        log.error("Unsupported config: name=%s, value=%s", config_name, config_value)


def apm_tracing_rc(lib_config, dd_config):
    # Convert configuration from a string to the format expected
    # by the global config object
    config_mapping = {
        "_trace_sampling_rules": _convert_rc_trace_sampling_rules(lib_config),
        "_logs_injection": _convert_optional_bool(lib_config, "log_injection_enabled"),
        "tags": _convert_rc_tags(lib_config, "tracing_tags", dd_config),
        "_tracing_enabled": _convert_optional_bool(lib_config, "tracing_enabled"),
        "_trace_http_header_tags": _convert_rc_tags(lib_config, "tracing_header_tags", dd_config),
    }

    for config_name, new_rc_value in config_mapping.items():
        config_item = dd_config._config[config_name]
        # Only proceed if value actually changed
        if config_item._rc_value != new_rc_value:
            config_item.set_value(new_rc_value, "remote_config")
            _apply_config_change(config_name, config_item.value(), dd_config)

    log.debug("APM Tracing Received: %s from the Agent", lib_config)
