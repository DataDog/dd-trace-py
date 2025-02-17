import os
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ..constants import ENV_KEY
from ..constants import VERSION_KEY
from ..internal.logger import get_logger
from ..internal.telemetry import telemetry_writer
from ..internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)


OTEL_UNIFIED_TAG_MAPPINGS = {
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
        if style in ["b3", "b3multi", "datadog", "tracecontext", "none"]:
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
        rate = os.environ.get("OTEL_TRACES_SAMPLER_ARG", "1")

    if rate is not None:
        return f'[{{"sample_rate":{rate}}}]'
    return None


def _remap_traces_exporter(otel_value: str) -> Optional[str]:
    """Remaps the otel trace exporter to ddtrace trace enabled"""
    if otel_value == "none":
        return "False"
    return None


def _remap_metrics_exporter(otel_value: str) -> Optional[str]:
    """Remaps the otel metrics exporter to ddtrace metrics exporter"""
    if otel_value == "none":
        return "False"
    return None


def _remap_otel_tags(otel_value: str) -> Optional[str]:
    """Remaps the otel tags to ddtrace tags"""
    dd_tags: List[str] = []

    try:
        otel_user_tag_dict: Dict[str, str] = dict()
        for tag in otel_value.split(","):
            key, value = tag.split("=")
            otel_user_tag_dict[key] = value

        for key, value in otel_user_tag_dict.items():
            if key.lower() in OTEL_UNIFIED_TAG_MAPPINGS:
                dd_key = OTEL_UNIFIED_TAG_MAPPINGS[key.lower()]
                dd_tags.insert(0, f"{dd_key}:{value}")
            else:
                dd_tags.append(f"{key}:{value}")
    except Exception:
        return None

    if len(dd_tags) > 10:
        dd_tags, remaining_tags = dd_tags[:10], dd_tags[10:]
        log.warning(
            "To preserve metrics cardinality, only the following first 10 tags have been processed %s. "
            "The following tags were not ingested: %s",
            dd_tags,
            remaining_tags,
        )
    return ",".join(dd_tags)


def _remap_otel_sdk_config(otel_value: str) -> Optional[str]:
    """Remaps the otel sdk config to ddtrace sdk config"""
    if otel_value == "false":
        return "True"
    elif otel_value == "true":
        return "False"
    return None


def _remap_default(otel_value: str) -> Optional[str]:
    """Remaps the otel default value to ddtrace default value"""
    return otel_value


ENV_VAR_MAPPINGS: Dict[str, Tuple[str, Callable[[str], Optional[str]]]] = {
    "OTEL_SERVICE_NAME": ("DD_SERVICE", _remap_default),
    "OTEL_LOG_LEVEL": ("DD_TRACE_DEBUG", _remap_otel_log_level),
    "OTEL_PROPAGATORS": ("DD_TRACE_PROPAGATION_STYLE", _remap_otel_propagators),
    "OTEL_TRACES_SAMPLER": ("DD_TRACE_SAMPLING_RULES", _remap_traces_sampler),
    "OTEL_TRACES_EXPORTER": ("DD_TRACE_ENABLED", _remap_traces_exporter),
    "OTEL_METRICS_EXPORTER": ("DD_RUNTIME_METRICS_ENABLED", _remap_metrics_exporter),
    "OTEL_RESOURCE_ATTRIBUTES": ("DD_TAGS", _remap_otel_tags),
    "OTEL_SDK_DISABLED": ("DD_TRACE_OTEL_ENABLED", _remap_otel_sdk_config),
}


def validate_otel_envs():
    user_envs = {key.upper(): value for key, value in os.environ.items()}
    for otel_env, _ in user_envs.items():
        if (
            otel_env not in ENV_VAR_MAPPINGS
            and otel_env.startswith("OTEL_")
            and otel_env not in ("OTEL_PYTHON_CONTEXT", "OTEL_TRACES_SAMPLER_ARG", "OTEL_LOGS_EXPORTER")
        ):
            _unsupported_otel_config(otel_env)
        elif otel_env == "OTEL_LOGS_EXPORTER":
            # check for invalid values
            otel_value = os.environ.get(otel_env, "none").lower()
            if otel_value != "none":
                _invalid_otel_config(otel_env)
            telemetry_writer.add_configuration(otel_env, otel_value, "env_var")


def should_parse_otel_env(otel_env):
    dd_env, _ = ENV_VAR_MAPPINGS[otel_env]
    if otel_env in os.environ and dd_env in os.environ:
        _hiding_otel_config(otel_env, dd_env)
        return False
    return otel_env in os.environ


def parse_otel_env(otel_env: str) -> Optional[str]:
    _, otel_config_validator = ENV_VAR_MAPPINGS[otel_env]
    raw_value = os.environ.get(otel_env, "")
    if otel_env not in ("OTEL_RESOURCE_ATTRIBUTES", "OTEL_SERVICE_NAME"):
        # Resource attributes and service name are case-insensitive
        raw_value = raw_value.lower()
    mapped_value = otel_config_validator(raw_value)
    if mapped_value is None:
        _invalid_otel_config(otel_env)
        return None
    return mapped_value


def _hiding_otel_config(otel_env, dd_env):
    log.debug(
        "Datadog configuration %s is already set. OpenTelemetry configuration will be ignored: %s=%s",
        dd_env,
        otel_env,
        os.environ[otel_env],
    )
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "otel.env.hiding",
        1,
        (("config_opentelemetry", otel_env.lower()), ("config_datadog", dd_env.lower())),
    )


def _invalid_otel_config(otel_env):
    log.warning(
        "Setting %s to %s is not supported by ddtrace, this configuration will be ignored.",
        otel_env,
        os.environ.get(otel_env, ""),
    )
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "otel.env.invalid",
        1,
        (("config_opentelemetry", otel_env.lower()),),
    )


def _unsupported_otel_config(otel_env):
    log.warning("OpenTelemetry configuration %s is not supported by Datadog.", otel_env)
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "otel.env.unsupported",
        1,
        (("config_opentelemetry", otel_env.lower()),),
    )
