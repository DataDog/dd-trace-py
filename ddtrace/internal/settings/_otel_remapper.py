from typing import Callable
from typing import Optional

from ddtrace.constants import ENV_KEY
from ddtrace.constants import VERSION_KEY
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env


log = get_logger(__name__)

# Returned by a remapper when a valid OpenTelemetry value leaves the Datadog setting unchanged.
NO_OVERRIDE = "__otel_no_override__"

OTEL_UNIFIED_TAG_MAPPINGS = {
    "deployment.environment.name": ENV_KEY,
    "deployment.environment": ENV_KEY,
    "service.name": "service",
    "service.version": VERSION_KEY,
}


def _remap_otel_log_level(otel_value: str) -> Optional[str]:
    """Remaps the otel log level to ddtrace log level"""
    if otel_value == "debug":
        return "True"
    return None


def _remap_otel_propagators(otel_value: str) -> Optional[str]:
    """Remaps the otel propagators to ddtrace propagators"""
    accepted_styles = []
    for style in otel_value.split(","):
        style = style.strip().lower()
        if style in ["b3", "b3multi", "baggage", "datadog", "tracecontext", "none"]:
            if style not in accepted_styles:
                accepted_styles.append(style)
        else:
            log.warning("Following style not supported by ddtrace: %s.", style)
    return ",".join(accepted_styles) or None


def _remap_traces_sampler(otel_value: str) -> Optional[str]:
    """Remaps the otel trace sampler to ddtrace trace sampler"""
    if otel_value in ["always_on", "always_off", "traceidratio"]:
        log.warning(
            "Trace sampler set from %s to parentbased_%s; only parent based sampling is supported.",
            otel_value,
            otel_value,
        )
        otel_value = f"parentbased_{otel_value}"
    rate = None
    if otel_value == "parentbased_always_on":
        rate = "1.0"
    elif otel_value == "parentbased_always_off":
        rate = "0.0"
    elif otel_value == "parentbased_traceidratio":
        rate = env.get("OTEL_TRACES_SAMPLER_ARG", "1")

    if rate is not None:
        return f'[{{"sample_rate":{rate}}}]'
    return None


_SUPPORTED_EXPORTERS = ("none", "otlp")

OTEL_EXPORTER_ENV_VARS = ("OTEL_TRACES_EXPORTER", "OTEL_METRICS_EXPORTER", "OTEL_LOGS_EXPORTER")


def _exporter_names(otel_value: str) -> list[str]:
    names = (name.strip().lower() for name in otel_value.split(","))
    return [name for name in names if name]


def is_unset_exporter_list(otel_value: str) -> bool:
    """Whether an OTEL_{TRACES,METRICS,LOGS}_EXPORTER value holds no entry, like an empty value."""
    return not _exporter_names(otel_value)


def _select(names: list[str]) -> Optional[str]:
    # otlp is the only exporter ddtrace supports. Combining it with none is not expected, so
    # picking otlp in that case is a compromise.
    if "otlp" in names:
        return "otlp"
    if "none" in names:
        return "none"
    return None


def has_ignored_exporter(otel_value: str) -> bool:
    """Whether an OTEL_{TRACES,METRICS,LOGS}_EXPORTER list selects an exporter but ignores other entries."""
    names = _exporter_names(otel_value)
    selected = _select(names)
    return selected is not None and any(name != selected for name in names)


def select_exporter(otel_env: str, otel_value: str, warn: bool = False) -> Optional[str]:
    """Select the exporter to use from an OTEL_{TRACES,METRICS,LOGS}_EXPORTER value.

    The OpenTelemetry spec allows the value to be a comma-separated list of exporters. ddtrace does
    not export to several exporters at once: it falls back to the supported entry of the list and
    ignores the others. Entries are trimmed and matched ignoring case, and blank entries are
    skipped. Returns "otlp" or "none", or None when the list holds no supported exporter. When warn
    is true, every ignored entry of a list that selects an exporter is logged.
    """
    names = _exporter_names(otel_value)
    selected = _select(names)
    if selected is None:
        return None

    if warn:
        for name in names:
            if name == selected:
                continue
            if name in _SUPPORTED_EXPORTERS:
                log.warning("%s: ignoring %s combined with %s.", otel_env, name, selected)
            else:
                log.warning("%s: ignoring unsupported exporter %s.", otel_env, name)
    return selected


def _remap_traces_exporter(otel_value: str) -> Optional[str]:
    """Remaps the otel trace exporter to ddtrace trace enabled"""
    exporter = select_exporter("OTEL_TRACES_EXPORTER", otel_value, warn=True)
    if exporter == "none":
        return "False"
    if exporter == "otlp":
        # OTLP export is handled natively by ddtrace; tracing remains enabled.
        return "True"
    return None


def _remap_metrics_exporter(otel_value: str) -> Optional[str]:
    """Remaps the otel metrics exporter to ddtrace metrics exporter"""
    if select_exporter("OTEL_METRICS_EXPORTER", otel_value) == "none":
        return "False"
    return None


def _remap_logs_exporter(otel_value: str) -> Optional[str]:
    """Remaps the otel logs exporter to ddtrace logs enabled"""
    exporter = select_exporter("OTEL_LOGS_EXPORTER", otel_value, warn=True)
    if exporter == "none":
        return "False"
    if exporter == "otlp":
        return "True"
    return None


def _remap_otel_tags(otel_value: str) -> Optional[str]:
    """Remaps the otel tags to ddtrace tags"""
    dd_tags: list[str] = []

    try:
        otel_user_tag_dict: dict[str, str] = dict()
        for tag in otel_value.split(","):
            key, value = tag.split("=")
            otel_user_tag_dict[key] = value

        has_stable_environment = any(key.lower() == "deployment.environment.name" for key in otel_user_tag_dict)
        for key, value in otel_user_tag_dict.items():
            normalized_key = key.lower()
            if normalized_key == "deployment.environment" and has_stable_environment:
                continue
            if normalized_key in OTEL_UNIFIED_TAG_MAPPINGS:
                dd_key = OTEL_UNIFIED_TAG_MAPPINGS[normalized_key]
                dd_tags.insert(0, f"{dd_key}:{value}")
            else:
                dd_tags.append(f"{key}:{value}")
    except Exception:
        return None

    if len(dd_tags) > 10:
        dd_tags, remaining_tags = dd_tags[:10], dd_tags[10:]
        log.warning(
            "To reduce cardinality, only the first 10 tags derived from OpenTelemetry resource attributes were "
            "kept: %s. Reduce the number of resource attributes to avoid dropping tags. Dropped tags: %s",
            dd_tags,
            remaining_tags,
        )
    return ",".join(dd_tags)


def _remap_otel_sdk_config(otel_value: str) -> Optional[str]:
    """Remaps OTEL_SDK_DISABLED to a ddtrace enabled setting.

    OTEL_SDK_DISABLED=true disables the setting. False is the specification default, and an empty value is
    treated as unset, so neither enables anything.
    """
    if otel_value == "true":
        return "False"
    elif otel_value in ("false", ""):
        return NO_OVERRIDE
    return None


def _remap_default(otel_value: str) -> Optional[str]:
    """Remaps the otel default value to ddtrace default value"""
    return otel_value


ENV_VAR_MAPPINGS: dict[str, tuple[str, Callable[[str], Optional[str]]]] = {
    "OTEL_SERVICE_NAME": ("DD_SERVICE", _remap_default),
    "OTEL_LOG_LEVEL": ("DD_TRACE_DEBUG", _remap_otel_log_level),
    "OTEL_PROPAGATORS": ("DD_TRACE_PROPAGATION_STYLE", _remap_otel_propagators),
    "OTEL_TRACES_SAMPLER": ("DD_TRACE_SAMPLING_RULES", _remap_traces_sampler),
    "OTEL_TRACES_EXPORTER": ("DD_TRACE_ENABLED", _remap_traces_exporter),
    "OTEL_METRICS_EXPORTER": ("DD_RUNTIME_METRICS_ENABLED", _remap_metrics_exporter),
    "OTEL_LOGS_EXPORTER": ("DD_LOGS_OTEL_ENABLED", _remap_logs_exporter),
    "OTEL_RESOURCE_ATTRIBUTES": ("DD_TAGS", _remap_otel_tags),
    "OTEL_SDK_DISABLED": ("DD_TRACE_OTEL_ENABLED", _remap_otel_sdk_config),
}

# https://github.com/open-telemetry/opentelemetry-python/blob/v1.34.1/opentelemetry-sdk/src/opentelemetry/sdk/environment_variables/__init__.py
SUPPORTED_OTEL_ENV_VARS = {
    "OTEL_PYTHON_CONTEXT",
    "OTEL_METRIC_EXPORT_INTERVAL",
    "OTEL_METRIC_EXPORT_TIMEOUT",
    "OTEL_TRACES_SAMPLER_ARG",
    "OTEL_TRACES_SPAN_METRICS_ENABLED",
    "OTEL_EXPORTER_OTLP_TIMEOUT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_COMPRESSION",
    "OTEL_EXPORTER_OTLP_CERTIFICATE",
    "OTEL_LOGS_EXPORTER",
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_LOGS_CLIENT_KEY",
    "OTEL_EXPORTER_OTLP_LOGS_CLIENT_CERTIFICATE",
    "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
    "OTEL_EXPORTER_OTLP_LOGS_INSECURE",
    "OTEL_EXPORTER_OTLP_LOGS_COMPRESSION",
    "OTEL_EXPORTER_OTLP_LOGS_TIMEOUT",
    "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_CERTIFICATE",
    "OTEL_EXPORTER_OTLP_METRICS_CLIENT_KEY",
    "OTEL_EXPORTER_OTLP_METRICS_CLIENT_CERTIFICATE",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_INSECURE",
    "OTEL_EXPORTER_OTLP_METRICS_COMPRESSION",
    "OTEL_EXPORTER_OTLP_METRICS_TIMEOUT",
    "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE",
    "OTEL_EXPORTER_OTLP_TRACES_CLIENT_KEY",
    "OTEL_EXPORTER_OTLP_TRACES_CLIENT_CERTIFICATE",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_INSECURE",
    "OTEL_EXPORTER_OTLP_TRACES_COMPRESSION",
    "OTEL_EXPORTER_OTLP_TRACES_TIMEOUT",
}


def parse_otel_env(otel_env: str) -> tuple[str, Optional[str]]:
    """Returns the raw and remapped values of otel_env.

    The remapped value is None when the value is invalid, and NO_OVERRIDE when it is valid but does not
    change the Datadog setting.
    """
    _, otel_config_validator = ENV_VAR_MAPPINGS[otel_env]
    raw_value = env.get(otel_env, "")
    if otel_env not in ("OTEL_RESOURCE_ATTRIBUTES", "OTEL_SERVICE_NAME"):
        # Resource attributes and service name are case-insensitive
        raw_value = raw_value.lower()
    mapped_value = otel_config_validator(raw_value)
    if mapped_value is None:
        return "", None
    return raw_value, mapped_value
