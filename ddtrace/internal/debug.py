import datetime
import logging
import platform
import sys
from typing import Any
from typing import Optional
from typing import Union
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from ddtrace import _monkey
from ddtrace.internal import agent
from ddtrace.internal import core
from ddtrace.internal.constants import _PROPAGATION_STYLE_NONE
from ddtrace.internal.constants import RUNTIME_METRICS_ENABLED_KEY
from ddtrace.internal.packages import get_distributions
from ddtrace.internal.settings import env
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings._opentelemetry import otel_config
from ddtrace.internal.settings._opentelemetry import otel_resource_attributes
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.utils.tracer_debug_info import TracerDebugInfo
from ddtrace.internal.writer import AgentWriterInterface
from ddtrace.internal.writer import LogWriter
from ddtrace.internal.writer import NativeWriter
from ddtrace.version import __version__

from .logger import get_logger


logger = get_logger(__name__)

# The interpreter bit width, as platform.architecture() reports it. That function spawns the file command, which
# imports subprocess and threading. collect() runs in a background thread at startup, and with module cloning the
# thread that first imports threading becomes the application's main thread, which breaks gevent.
_ARCHITECTURE = "64bit" if sys.maxsize > 2**32 else "32bit"


def in_venv() -> bool:
    # Works with both venv and virtualenv
    # https://stackoverflow.com/a/42580137
    return (
        "VIRTUAL_ENV" in env
        or hasattr(sys, "real_prefix")
        or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
    )


def tags_to_str(tags: dict[str, Any]) -> str:
    # Turn a dict of tags to a string "k1:v1,k2:v2,..."
    return ",".join(["%s:%s" % (k, v) for k, v in tags.items()])


_REDACTED = "<redacted>"


def _sanitize_otlp_endpoint(endpoint: Optional[str]) -> Optional[str]:
    """Remove the parts of an OTLP endpoint that can hold credentials: user info, query, and fragment."""
    if not endpoint:
        return endpoint
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return None
    if parts.netloc:
        return urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, "", ""))
    # Not a URL with a host, such as the host:port form gRPC accepts.
    return endpoint.split("?", 1)[0].split("#", 1)[0].rpartition("@")[2]


def _redact_otlp_headers(headers: Optional[str]) -> dict[str, str]:
    """Report the names of the configured OTLP headers only, as their values often hold API keys."""
    names = {}
    for item in (headers or "").split(","):
        name, sep, _ = item.partition("=")
        name = name.strip().lower()
        if sep and name:
            names[name] = _REDACTED
    return names


def _otel_traces_sampler(sampling_rules: list[Any]) -> tuple[str, Optional[float]]:
    """Return the OpenTelemetry sampler equivalent to the root sampling, and its ratio when it has one.

    Spans with a propagated sampling decision are never resampled, so every equivalent is parent-based.
    Datadog sampling without an OpenTelemetry equivalent gets a datadog_* name describing what drives it.
    """
    if not sampling_rules:
        return "datadog_agent_rates", None
    # The first matching rule applies, so a first rule that matches every span sets a fixed rate.
    rule = sampling_rules[0]
    if rule.service is not None or rule.name is not None or rule.resource is not None or rule.tags:
        return "datadog_custom_rules", None
    if rule.sample_rate == 1:
        return "parentbased_always_on", None
    if rule.sample_rate == 0:
        return "parentbased_always_off", None
    return "parentbased_traceidratio", rule.sample_rate


def _otel_propagators() -> Optional[str]:
    """Return the OTEL_PROPAGATORS equivalent of the propagation styles.

    Returns None when injection and extraction differ, since no single OTEL_PROPAGATORS value describes them.
    """
    inject = [style for style in config._propagation_style_inject or () if style != _PROPAGATION_STYLE_NONE]
    extract = [style for style in config._propagation_style_extract or () if style != _PROPAGATION_STYLE_NONE]
    if inject != extract:
        return None
    return ",".join(inject) or _PROPAGATION_STYLE_NONE


def _otel_log_level() -> str:
    """Return the OTEL_LOG_LEVEL name of the minimum level the ddtrace logger emits."""
    level = logging.getLogger("ddtrace").getEffectiveLevel()
    if level < logging.DEBUG:
        return "trace"
    if level < logging.INFO:
        return "debug"
    if level < logging.WARNING:
        return "info"
    if level < logging.ERROR:
        return "warn"
    if level < logging.CRITICAL:
        return "error"
    return "fatal"


def _collect_otel(tracer_info: TracerDebugInfo) -> dict[str, Any]:
    """Collect the effective OpenTelemetry configuration, keyed by the environment variable that sets each value.

    Every supported setting is reported, including those of the disabled signals. Header values are redacted.
    """
    exporter = otel_config.exporter
    writer = tracer_info.writer
    otlp_traces = config._tracing_enabled and isinstance(writer, NativeWriter) and writer.otlp_endpoint is not None
    sampler, sampler_arg = _otel_traces_sampler(tracer_info.sampling_rules)

    info: dict[str, Any] = {
        # Traces
        "DD_TRACE_OTEL_ENABLED": config._otel_trace_enabled,
        "DD_TRACE_OTEL_SEMANTICS_ENABLED": config._otel_trace_semantics_enabled,
        "OTEL_TRACES_EXPORTER": "otlp" if otlp_traces else "none",
        "OTEL_TRACES_SAMPLER": sampler,
    }
    if sampler_arg is not None:
        info["OTEL_TRACES_SAMPLER_ARG"] = sampler_arg
    info.update(
        {
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": _sanitize_otlp_endpoint(exporter.TRACES_ENDPOINT),
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": exporter.TRACES_PROTOCOL,
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": _redact_otlp_headers(exporter.TRACES_HEADERS),
            # Logs
            "DD_LOGS_OTEL_ENABLED": config._otel_logs_enabled,
            "OTEL_LOGS_EXPORTER": "otlp" if config._otel_logs_enabled else "none",
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": _sanitize_otlp_endpoint(exporter.LOGS_ENDPOINT),
            "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": exporter.LOGS_PROTOCOL,
            "OTEL_EXPORTER_OTLP_LOGS_HEADERS": _redact_otlp_headers(exporter.LOGS_HEADERS),
            # Metrics
            "DD_METRICS_OTEL_ENABLED": config._otel_metrics_enabled,
            "OTEL_METRIC_EXPORT_INTERVAL": exporter.METRICS_METRIC_READER_EXPORT_INTERVAL,
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": _sanitize_otlp_endpoint(exporter.METRICS_ENDPOINT),
            "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": exporter.METRICS_PROTOCOL,
            "OTEL_EXPORTER_OTLP_METRICS_HEADERS": _redact_otlp_headers(exporter.METRICS_HEADERS),
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": exporter.METRICS_TEMPORALITY_PREFERENCE,
            # Settings shared by every signal
            "OTEL_EXPORTER_OTLP_ENDPOINT": _sanitize_otlp_endpoint(exporter.ENDPOINT),
            "OTEL_EXPORTER_OTLP_PROTOCOL": exporter.PROTOCOL,
            "OTEL_EXPORTER_OTLP_HEADERS": _redact_otlp_headers(exporter.HEADERS),
            "OTEL_LOG_LEVEL": _otel_log_level(),
            "OTEL_SERVICE_NAME": config.service,
            "OTEL_RESOURCE_ATTRIBUTES": otel_resource_attributes(),
            "OTEL_PROPAGATORS": _otel_propagators(),
        }
    )
    return info


def collect(tracer_info: TracerDebugInfo) -> dict[str, Any]:
    """Collect system and library information into a serializable dict."""

    # Inline expensive imports to avoid unnecessary overhead on startup.
    from ddtrace.internal import gitmetadata
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config

    if isinstance(tracer_info.writer, LogWriter):
        agent_url = "AGENTLESS"
        agent_error = None
    elif isinstance(tracer_info.writer, AgentWriterInterface):
        writer = tracer_info.writer
        agent_url = writer.intake_url
        reachable = agent.is_reachable(agent_url)  # type: ignore[no-untyped-call]
        agent_error = None if reachable else "Agent not reachable at %s" % agent_url
    else:
        agent_url = "CUSTOM"
        agent_error = None

    sampling_rules = [str(rule) for rule in tracer_info.sampling_rules]

    is_venv = in_venv()

    packages_available = {name: version for (name, version) in get_distributions().items()}
    integration_configs: dict[str, Union[dict[str, Any], str]] = {}
    for module, enabled in _monkey.PATCH_MODULES.items():
        # TODO: this check doesn't work in all cases... we need a mapping
        #       between the module and the library name.
        module_available = module in packages_available
        module_instrumented = module in _monkey._PATCHED_MODULES
        module_imported = module in sys.modules

        if enabled:
            # Note that integration configs aren't added until the integration
            # module is imported. This typically occurs as a side-effect of
            # patch().
            # This also doesn't load work in all cases since we don't always
            # name the configuration entry the same as the integration module
            # name :/
            module_config = config._config.get(module, "N/A")
        else:
            module_config = None

        if module_available:
            integration_configs[module] = dict(
                enabled=enabled,
                instrumented=module_instrumented,
                module_version=packages_available[module],
                module_imported=module_imported,
                config=module_config,
            )

    pip_version = packages_available.get("pip", "N/A")
    git_repository_url, git_commit_sha, git_main_package = gitmetadata.get_git_tags()

    info: dict[str, Any] = dict(
        # Timestamp UTC ISO 8601 with the trailing +00:00 removed
        date=datetime.datetime.now(datetime.timezone.utc).isoformat()[0:-6],
        # eg. "Linux", "Darwin"
        os_name=platform.system(),
        # eg. 12.5.0
        os_version=platform.release(),
        is_64_bit=sys.maxsize > 2**32,
        architecture=_ARCHITECTURE,
        vm=platform.python_implementation(),
        version=__version__,
        lang="python",
        lang_version=platform.python_version(),
        pip_version=pip_version,
        in_virtual_env=is_venv,
        agent_url=agent_url,
        agent_error=agent_error,
        statsd_url=agent_config.dogstatsd_url,
        env=config.env or "",
        ddtrace_enabled=config._tracing_enabled,
        sampling_rules=sampling_rules,
        service=config.service or "",
        debug=logger.isEnabledFor(logging.DEBUG),
        enabled_cli="ddtrace" in env.get("PYTHONPATH", ""),
        log_injection_enabled=config._logs_injection,
        health_metrics_enabled=config._health_metrics_enabled,
        runtime_metrics_enabled=bool(core.root.get_item(RUNTIME_METRICS_ENABLED_KEY)),
        dd_version=config.version or "",
        global_tags=tags_to_str(config.tags),
        tracer_tags=tags_to_str(tracer_info.tags),
        integrations=integration_configs,
        partial_flush_enabled=tracer_info.partial_flush_enabled,
        partial_flush_min_spans=tracer_info.partial_flush_min_spans,
        asm_enabled=asm_config._asm_enabled,
        iast_enabled=asm_config._iast_enabled,
        waf_timeout=asm_config._waf_timeout,
        remote_config_enabled=config._remote_config_enabled,
        config_endpoint=config._from_endpoint,
        crashtracking_enabled=crashtracker_config.enabled,
        gitmetadata_enabled=gitmetadata.config.enabled,
        git_repository_url=git_repository_url,
        git_commit_sha=git_commit_sha,
        git_main_package=git_main_package,
        log_level_override=env.get("DD_TRACE_LOG_LEVEL"),
    )
    info.update(_collect_otel(tracer_info))
    return info


def pretty_collect(tracer: Any, color=True):
    class bcolors:
        HEADER = "\033[95m"
        OKBLUE = "\033[94m"
        OKCYAN = "\033[96m"
        OKGREEN = "\033[92m"
        WARNING = "\033[93m"
        FAIL = "\033[91m"
        ENDC = "\033[0m"
        BOLD = "\033[1m"

    if not color:
        bcolors.HEADER = ""
        bcolors.OKBLUE = ""
        bcolors.OKCYAN = ""
        bcolors.OKGREEN = ""
        bcolors.WARNING = ""
        bcolors.FAIL = ""
        bcolors.ENDC = ""
        bcolors.BOLD = ""

    info = collect(TracerDebugInfo.from_tracer(tracer))

    info_pretty = """{blue}{bold}Tracer Configurations:{end}
    Tracer enabled: {tracer_enabled}
    Application Security enabled: {appsec_enabled}
    Remote Configuration enabled: {remote_config_enabled}
    IAST enabled (experimental): {iast_enabled}
    Debug logging: {debug}
    Writing traces to: {agent_url}
    Agent error: {agent_error}
    Log injection enabled: {log_injection_enabled}
    Health metrics enabled: {health_metrics_enabled}
    Priority sampling enabled: {priority_sampling_enabled}
    Partial flushing enabled: {partial_flush_enabled}
    Partial flush minimum number of spans: {partial_flush_min_spans}
    WAF timeout: {waf_timeout} msecs
    {green}{bold}Tagging:{end}
    DD Service: {service}
    DD Env: {env}
    DD Version: {dd_version}
    Global Tags: {global_tags}
    Tracer Tags: {tracer_tags}""".format(
        tracer_enabled=info.get("ddtrace_enabled"),
        appsec_enabled=info.get("asm_enabled"),
        remote_config_enabled=info.get("remote_config_enabled"),
        iast_enabled=info.get("iast_enabled"),
        debug=info.get("debug"),
        agent_url=info.get("agent_url") or "Not writing at the moment, is your tracer running?",
        agent_error=info.get("agent_error") or "None",
        log_injection_enabled=info.get("log_injection_enabled"),
        health_metrics_enabled=info.get("health_metrics_enabled"),
        priority_sampling_enabled=info.get("priority_sampling_enabled"),
        partial_flush_enabled=info.get("partial_flush_enabled"),
        partial_flush_min_spans=info.get("partial_flush_min_spans") or "Not set",
        service=info.get("service") or "None",
        env=info.get("env") or "None",
        dd_version=info.get("dd_version") or "None",
        global_tags=info.get("global_tags") or "None",
        waf_timeout=info.get("waf_timeout"),
        tracer_tags=info.get("tracer_tags") or "None",
        blue=bcolors.OKBLUE,
        green=bcolors.OKGREEN,
        bold=bcolors.BOLD,
        end=bcolors.ENDC,
    )

    summary = f"{bcolors.OKCYAN}{bcolors.BOLD}Summary{bcolors.ENDC}"

    if info.get("agent_error"):
        summary += (
            "\n\n{fail}ERROR: It looks like you have an agent error: '{agent_error}'\n If you're experiencing"
            " a connection error, please make sure you've followed the setup for your particular environment so that "
            "the tracer and Datadog agent are configured properly to connect, and that the Datadog agent is running: "
            "https://ddtrace.readthedocs.io/en/stable/troubleshooting.html#failed-to-send-traces-connectionrefused"
            "error"
            "\nIf your issue is not a connection error then please reach out to support for further assistance:"
            " https://docs.datadoghq.com/help/{end}"
        ).format(fail=bcolors.FAIL, agent_error=info.get("agent_error"), end=bcolors.ENDC)

    if not info.get("service"):
        summary += (
            f"\n\n{bcolors.WARNING}WARNING SERVICE NOT SET: It is recommended that a service tag be set for all traced"
            " applications. For more information please see"
            f" https://ddtrace.readthedocs.io/en/stable/troubleshooting.html{bcolors.ENDC}"
        )

    if not info.get("env"):
        summary += (
            f"\n\n{bcolors.WARNING}WARNING ENV NOT SET: It is recommended that an env tag be set for all traced"
            " applications. For more information please see "
            f"https://ddtrace.readthedocs.io/en/stable/troubleshooting.html{bcolors.ENDC}"
        )

    if not info.get("dd_version"):
        summary += (
            f"\n\n{bcolors.WARNING}WARNING VERSION NOT SET: It is recommended that a version tag be set for all traced"
            " applications. For more information please see"
            f" https://ddtrace.readthedocs.io/en/stable/troubleshooting.html{bcolors.ENDC}"
        )

    info_pretty += "\n\n" + summary

    return info_pretty
