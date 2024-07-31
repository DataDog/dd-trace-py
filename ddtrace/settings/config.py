from copy import deepcopy
import json
import os
import re
import sys
from typing import Any  # noqa:F401
from typing import Callable  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Tuple  # noqa:F401
from typing import Union  # noqa:F401

from ddtrace.internal._file_queue import File_Queue
from ddtrace.internal.serverless import in_azure_function
from ddtrace.internal.serverless import in_gcp_function
from ddtrace.internal.utils.cache import cachedmethod
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from ..internal import gitmetadata
from ..internal.constants import _PROPAGATION_STYLE_DEFAULT
from ..internal.constants import DEFAULT_BUFFER_SIZE
from ..internal.constants import DEFAULT_MAX_PAYLOAD_SIZE
from ..internal.constants import DEFAULT_PROCESSING_INTERVAL
from ..internal.constants import DEFAULT_REUSE_CONNECTIONS
from ..internal.constants import DEFAULT_SAMPLING_RATE_LIMIT
from ..internal.constants import DEFAULT_TIMEOUT
from ..internal.constants import PROPAGATION_STYLE_ALL
from ..internal.constants import PROPAGATION_STYLE_B3_SINGLE
from ..internal.logger import get_logger
from ..internal.schema import DEFAULT_SPAN_SERVICE_NAME
from ..internal.serverless import in_aws_lambda
from ..internal.utils.formats import asbool
from ..internal.utils.formats import parse_tags_str
from ..pin import Pin
from ._otel_remapper import otel_remapping as _otel_remapping
from .http import HttpConfig
from .integration import IntegrationConfig


if sys.version_info >= (3, 8):
    from typing import Literal  # noqa:F401
else:
    from typing_extensions import Literal


log = get_logger(__name__)


DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP_DEFAULT = (
    r"(?ix)"
    r"(?:"  # JSON-ish leading quote
    r'(?:"|%22)?'
    r")"
    r"(?:"  # common keys"
    r"(?:old[-_]?|new[-_]?)?p(?:ass)?w(?:or)?d(?:1|2)?"  # pw, password variants
    r"|pass(?:[-_]?phrase)?"  # pass, passphrase variants
    r"|secret"
    r"|(?:"  # key, key_id variants
    r"api[-_]?"
    r"|private[-_]?"
    r"|public[-_]?"
    r"|access[-_]?"
    r"|secret[-_]?"
    r"|app(?:lica"
    r"tion)?[-_]?"
    r")key(?:[-_]?id)?"
    r"|token"
    r"|consumer[-_]?(?:id|key|secret)"
    r"|sign(?:ed|ature)?"
    r"|auth(?:entication|orization)?"
    r")"
    r"(?:"
    # '=' query string separator, plus value til next '&' separator
    r"(?:\s|%20)*(?:=|%3D)[^&]+"
    # JSON-ish '": "somevalue"', key being handled with case above, without the opening '"'
    r'|(?:"|%22)'  # closing '"' at end of key
    r"(?:\s|%20)*(?::|%3A)(?:\s|%20)*"  # ':' key-value separator, with surrounding spaces
    r'(?:"|%22)'  # opening '"' at start of value
    r'(?:%2[^2]|%[^2]|[^"%])+'  # value
    r'(?:"|%22)'  # closing '"' at end of value
    r")"
    r"|(?:"  # other common secret values
    r" bearer(?:\s|%20)+[a-z0-9._\-]+"
    r"|token(?::|%3A)[a-z0-9]{13}"
    r"|gh[opsu]_[0-9a-zA-Z]{36}"
    r"|ey[I-L](?:[\w=-]|%3D)+\.ey[I-L](?:[\w=-]|%3D)+(?:\.(?:[\w.+/=-]|%3D|%2F|%2B)+)?"
    r"|-{5}BEGIN(?:[a-z\s]|%20)+PRIVATE(?:\s|%20)KEY-{5}[^\-]+-{5}END"
    r"(?:[a-z\s]|%20)+PRIVATE(?:\s|%20)KEY(?:-{5})?(?:\n|%0A)?"
    r"|(?:ssh-(?:rsa|dss)|ecdsa-[a-z0-9]+-[a-z0-9]+)(?:\s|%20|%09)+(?:[a-z0-9/.+]"
    r"|%2F|%5C|%2B){100,}(?:=|%3D)*(?:(?:\s|%20|%09)+[a-z0-9._-]+)?"
    r")"
)


def _parse_propagation_styles(name, default):
    # type: (str, Optional[str]) -> Optional[List[str]]
    """Helper to parse http propagation extract/inject styles via env variables.

    The expected format is::

        <style>[,<style>...]


    The allowed values are:

    - "datadog"
    - "b3multi"
    - "b3" (formerly 'b3 single header')
    - "b3 single header (deprecated for 'b3')"
    - "tracecontext"
    - "none"


    The default value is ``"datadog,tracecontext"``.


    Examples::

        # Extract and inject b3 headers:
        DD_TRACE_PROPAGATION_STYLE="b3multi"

        # Disable header propagation:
        DD_TRACE_PROPAGATION_STYLE="none"

        # Extract trace context from "x-datadog-*" or "x-b3-*" headers from upstream headers
        DD_TRACE_PROPAGATION_STYLE_EXTRACT="datadog,b3multi"

        # Inject the "b3: *" header into downstream requests headers
        DD_TRACE_PROPAGATION_STYLE_INJECT="b3"
    """
    styles = []
    envvar = os.getenv(name, default=default)
    if envvar is None:
        return None
    for style in envvar.split(","):
        style = style.strip().lower()
        if style == "b3 single header":
            deprecate(
                'Using DD_TRACE_PROPAGATION_STYLE="b3 single header" is deprecated',
                message="Please use 'DD_TRACE_PROPAGATION_STYLE=\"b3\"' instead",
                removal_version="3.0.0",
                category=DDTraceDeprecationWarning,
            )
            style = PROPAGATION_STYLE_B3_SINGLE
        if not style:
            continue
        if style not in PROPAGATION_STYLE_ALL:
            raise ValueError(
                "Unknown style {!r} provided for {!r}, allowed values are {!r}".format(
                    style, name, PROPAGATION_STYLE_ALL
                )
            )
        styles.append(style)
    return styles


# Borrowed from: https://stackoverflow.com/questions/20656135/python-deep-merge-dictionary-data#20666342
def _deepmerge(source, destination):
    """
    Merge the first provided ``dict`` into the second.

    :param dict source: The ``dict`` to merge into ``destination``
    :param dict destination: The ``dict`` that should get updated
    :rtype: dict
    :returns: ``destination`` modified
    """
    for key, value in source.items():
        if isinstance(value, dict):
            # get node or create one
            node = destination.setdefault(key, {})
            _deepmerge(value, node)
        else:
            destination[key] = value

    return destination


def get_error_ranges(error_range_str):
    # type: (str) -> List[Tuple[int, int]]
    error_ranges = []
    error_range_str = error_range_str.strip()
    error_ranges_str = error_range_str.split(",")
    for error_range in error_ranges_str:
        values = error_range.split("-")
        try:
            # Note: mypy does not like variable type changing
            values = [int(v) for v in values]  # type: ignore[misc]
        except ValueError:
            log.exception("Error status codes was not a number %s", values)
            continue
        error_range = (min(values), max(values))  # type: ignore[assignment]
        error_ranges.append(error_range)
    return error_ranges  # type: ignore[return-value]


_ConfigSource = Literal["default", "env_var", "code", "remote_config"]
_JSONType = Union[None, int, float, str, bool, List["_JSONType"], Dict[str, "_JSONType"]]


class _ConfigItem:
    """Configuration item that tracks the value of a setting, and where it came from."""

    def __init__(self, name, default, envs):
        # type: (str, Union[_JSONType, Callable[[], _JSONType]], List[Tuple[str, Callable[[str], Any]]]) -> None
        self._name = name
        self._env_value: _JSONType = None
        self._code_value: _JSONType = None
        self._rc_value: _JSONType = None
        if callable(default):
            self._default_value = default()
        else:
            self._default_value = default
        self._envs = envs
        for env_var, parser in envs:
            if env_var in os.environ:
                self._env_value = parser(os.environ[env_var])
                break

    def set_value_source(self, value, source):
        # type: (Any, _ConfigSource) -> None
        if source == "code":
            self._code_value = value
        elif source == "remote_config":
            self._rc_value = value
        else:
            raise ValueError("Invalid source: {}".format(source))

    def set_code(self, value):
        # type: (_JSONType) -> None
        self._code_value = value

    def unset_rc(self):
        # type: () -> None
        self._rc_value = None

    def value(self):
        # type: () -> _JSONType
        if self._rc_value is not None:
            return self._rc_value
        if self._code_value is not None:
            return self._code_value
        if self._env_value is not None:
            return self._env_value
        return self._default_value

    def source(self):
        # type: () -> _ConfigSource
        if self._rc_value is not None:
            return "remote_config"
        if self._code_value is not None:
            return "code"
        if self._env_value is not None:
            return "env_var"
        return "default"

    def __repr__(self):
        return "<{} name={} default={} env_value={} user_value={} remote_config_value={}>".format(
            self.__class__.__name__,
            self._name,
            self._default_value,
            self._env_value,
            self._code_value,
            self._rc_value,
        )


def _parse_global_tags(s):
    # cleanup DD_TAGS, because values will be inserted back in the optimal way (via _dd.git.* tags)
    return gitmetadata.clean_tags(parse_tags_str(s))


def _default_config():
    # type: () -> Dict[str, _ConfigItem]
    return {
        "_trace_sample_rate": _ConfigItem(
            name="trace_sample_rate",
            default=1.0,
            envs=[("DD_TRACE_SAMPLE_RATE", float)],
        ),
        "_trace_sampling_rules": _ConfigItem(
            name="trace_sampling_rules",
            default=lambda: "",
            envs=[("DD_TRACE_SAMPLING_RULES", str)],
        ),
        "logs_injection": _ConfigItem(
            name="logs_injection",
            default=False,
            envs=[("DD_LOGS_INJECTION", asbool)],
        ),
        "trace_http_header_tags": _ConfigItem(
            name="trace_http_header_tags",
            default=lambda: {},
            envs=[("DD_TRACE_HEADER_TAGS", parse_tags_str)],
        ),
        "tags": _ConfigItem(
            name="tags",
            default=lambda: {},
            envs=[("DD_TAGS", _parse_global_tags)],
        ),
        "_tracing_enabled": _ConfigItem(
            name="tracing_enabled",
            default=True,
            envs=[("DD_TRACE_ENABLED", asbool)],
        ),
        "_profiling_enabled": _ConfigItem(
            name="profiling_enabled",
            default=False,
            envs=[("DD_PROFILING_ENABLED", asbool)],
        ),
        "_asm_enabled": _ConfigItem(
            name="asm_enabled",
            default=False,
            envs=[("DD_APPSEC_ENABLED", asbool)],
        ),
        "_sca_enabled": _ConfigItem(
            name="sca_enabled",
            default=None,
            envs=[("DD_APPSEC_SCA_ENABLED", asbool)],
        ),
        "_dsm_enabled": _ConfigItem(
            name="dsm_enabled",
            default=False,
            envs=[("DD_DATA_STREAMS_ENABLED", asbool)],
        ),
    }


class Config(object):
    """Configuration object that exposes an API to set and retrieve
    global settings for each integration. All integrations must use
    this instance to register their defaults, so that they're public
    available and can be updated by users.
    """

    class _HTTPServerConfig(object):
        _error_statuses = "500-599"  # type: str
        _error_ranges = get_error_ranges(_error_statuses)  # type: List[Tuple[int, int]]

        @property
        def error_statuses(self):
            # type: () -> str
            return self._error_statuses

        @error_statuses.setter
        def error_statuses(self, value):
            # type: (str) -> None
            self._error_statuses = value
            self._error_ranges = get_error_ranges(value)
            # Mypy can't catch cached method's invalidate()
            self.is_error_code.invalidate()  # type: ignore[attr-defined]

        @property
        def error_ranges(self):
            # type: () -> List[Tuple[int, int]]
            return self._error_ranges

        @cachedmethod()
        def is_error_code(self, status_code):
            # type: (int) -> bool
            """Returns a boolean representing whether or not a status code is an error code.
            Error status codes by default are 500-599.
            You may also enable custom error codes::

                from ddtrace import config
                config.http_server.error_statuses = '401-404,419'

            Ranges and singular error codes are permitted and can be separated using commas.
            """
            for error_range in self.error_ranges:
                if error_range[0] <= status_code <= error_range[1]:
                    return True
            return False

    def __init__(self):
        # Must map Otel configurations to Datadog configurations before creating the config object.
        _otel_remapping()
        # Must come before _integration_configs due to __setattr__
        self._config = _default_config()

        # Use a dict as underlying storing mechanism for integration configs
        self._integration_configs = {}

        self._debug_mode = asbool(os.getenv("DD_TRACE_DEBUG", default=False))
        self._startup_logs_enabled = asbool(os.getenv("DD_TRACE_STARTUP_LOGS", False))

        self._trace_rate_limit = int(os.getenv("DD_TRACE_RATE_LIMIT", default=DEFAULT_SAMPLING_RATE_LIMIT))
        self._partial_flush_enabled = asbool(os.getenv("DD_TRACE_PARTIAL_FLUSH_ENABLED", default=True))
        self._partial_flush_min_spans = int(os.getenv("DD_TRACE_PARTIAL_FLUSH_MIN_SPANS", default=300))
        self._priority_sampling = asbool(os.getenv("DD_PRIORITY_SAMPLING", default=True))

        self.http = HttpConfig(header_tags=self.trace_http_header_tags)
        self._remote_config_enabled = asbool(os.getenv("DD_REMOTE_CONFIGURATION_ENABLED", default=True))
        self._remote_config_poll_interval = float(
            os.getenv(
                "DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS", default=os.getenv("DD_REMOTECONFIG_POLL_SECONDS", default=5.0)
            )
        )
        self._trace_api = os.getenv("DD_TRACE_API_VERSION")
        self._trace_writer_buffer_size = int(
            os.getenv("DD_TRACE_WRITER_BUFFER_SIZE_BYTES", default=DEFAULT_BUFFER_SIZE)
        )
        self._trace_writer_payload_size = int(
            os.getenv("DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES", default=DEFAULT_MAX_PAYLOAD_SIZE)
        )
        self._trace_writer_interval_seconds = float(
            os.getenv("DD_TRACE_WRITER_INTERVAL_SECONDS", default=DEFAULT_PROCESSING_INTERVAL)
        )
        self._trace_writer_connection_reuse = asbool(
            os.getenv("DD_TRACE_WRITER_REUSE_CONNECTIONS", DEFAULT_REUSE_CONNECTIONS)
        )
        self._trace_writer_log_err_payload = asbool(os.environ.get("_DD_TRACE_WRITER_LOG_ERROR_PAYLOADS", False))

        self._trace_agent_hostname = os.environ.get("DD_AGENT_HOST", os.environ.get("DD_TRACE_AGENT_HOSTNAME"))
        self._trace_agent_port = os.environ.get("DD_AGENT_PORT", os.environ.get("DD_TRACE_AGENT_PORT"))
        self._trace_agent_url = os.environ.get("DD_TRACE_AGENT_URL")

        self._stats_agent_hostname = os.environ.get("DD_AGENT_HOST", os.environ.get("DD_DOGSTATSD_HOST"))
        self._stats_agent_port = os.getenv("DD_DOGSTATSD_PORT")
        self._stats_agent_url = os.getenv("DD_DOGSTATSD_URL")
        self._agent_timeout_seconds = float(os.getenv("DD_TRACE_AGENT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT))

        self._span_traceback_max_size = int(os.getenv("DD_TRACE_SPAN_TRACEBACK_MAX_SIZE", default=30))

        # Master switch for turning on and off trace search by default
        # this weird invocation of getenv is meant to read the DD_ANALYTICS_ENABLED
        # legacy environment variable. It should be removed in the future
        legacy_config_value = os.getenv("DD_ANALYTICS_ENABLED", default=False)

        self.analytics_enabled = asbool(os.getenv("DD_TRACE_ANALYTICS_ENABLED", default=legacy_config_value))
        self.client_ip_header = os.getenv("DD_TRACE_CLIENT_IP_HEADER")
        self.retrieve_client_ip = asbool(os.getenv("DD_TRACE_CLIENT_IP_ENABLED", default=False))

        self.propagation_http_baggage_enabled = asbool(
            os.getenv("DD_TRACE_PROPAGATION_HTTP_BAGGAGE_ENABLED", default=False)
        )

        self.env = os.getenv("DD_ENV") or self.tags.get("env")
        self.service = os.getenv("DD_SERVICE", default=self.tags.get("service", DEFAULT_SPAN_SERVICE_NAME))

        if self.service is None and in_gcp_function():
            self.service = os.environ.get("K_SERVICE", os.environ.get("FUNCTION_NAME"))
        if self.service is None and in_azure_function():
            self.service = os.environ.get("WEBSITE_SITE_NAME")

        self._extra_services = set()
        self._extra_services_queue = None if in_aws_lambda() or not self._remote_config_enabled else File_Queue()
        self.version = os.getenv("DD_VERSION", default=self.tags.get("version"))
        self.http_server = self._HTTPServerConfig()

        self._unparsed_service_mapping = os.getenv("DD_SERVICE_MAPPING", default="")
        self.service_mapping = parse_tags_str(self._unparsed_service_mapping)

        # The service tag corresponds to span.service and should not be
        # included in the global tags.
        if self.service and "service" in self.tags:
            del self.tags["service"]

        # The version tag should not be included on all spans.
        if self.version and "version" in self.tags:
            del self.tags["version"]

        self.report_hostname = asbool(os.getenv("DD_TRACE_REPORT_HOSTNAME", default=False))

        self.health_metrics_enabled = asbool(os.getenv("DD_TRACE_HEALTH_METRICS_ENABLED", default=False))

        self._telemetry_enabled = asbool(os.getenv("DD_INSTRUMENTATION_TELEMETRY_ENABLED", True))
        self._telemetry_heartbeat_interval = float(os.getenv("DD_TELEMETRY_HEARTBEAT_INTERVAL", "60"))
        self._telemetry_dependency_collection = asbool(os.getenv("DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED", True))

        self._runtime_metrics_enabled = asbool(os.getenv("DD_RUNTIME_METRICS_ENABLED", False))

        self._128_bit_trace_id_enabled = asbool(os.getenv("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", True))

        self._128_bit_trace_id_logging_enabled = asbool(os.getenv("DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED", False))

        self._sampling_rules = os.getenv("DD_SPAN_SAMPLING_RULES")
        self._sampling_rules_file = os.getenv("DD_SPAN_SAMPLING_RULES_FILE")

        # Propagation styles
        self._propagation_style_extract = self._propagation_style_inject = _parse_propagation_styles(
            "DD_TRACE_PROPAGATION_STYLE", default=_PROPAGATION_STYLE_DEFAULT
        )
        # DD_TRACE_PROPAGATION_STYLE_EXTRACT and DD_TRACE_PROPAGATION_STYLE_INJECT
        #  take precedence over DD_TRACE_PROPAGATION_STYLE
        propagation_style_extract = _parse_propagation_styles("DD_TRACE_PROPAGATION_STYLE_EXTRACT", default=None)
        if propagation_style_extract is not None:
            self._propagation_style_extract = propagation_style_extract

        propagation_style_inject = _parse_propagation_styles("DD_TRACE_PROPAGATION_STYLE_INJECT", default=None)
        if propagation_style_inject is not None:
            self._propagation_style_inject = propagation_style_inject

        self._propagation_extract_first = asbool(os.getenv("DD_TRACE_PROPAGATION_EXTRACT_FIRST", False))

        # Datadog tracer tags propagation
        x_datadog_tags_max_length = int(os.getenv("DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH", default=512))
        if x_datadog_tags_max_length < 0:
            raise ValueError(
                (
                    "Invalid value {!r} provided for DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH, "
                    "only non-negative values allowed"
                ).format(x_datadog_tags_max_length)
            )
        self._x_datadog_tags_max_length = x_datadog_tags_max_length
        self._x_datadog_tags_enabled = x_datadog_tags_max_length > 0

        # Raise certain errors only if in testing raise mode to prevent crashing in production with non-critical errors
        self._raise = asbool(os.getenv("DD_TESTING_RAISE", False))

        trace_compute_stats_default = in_gcp_function() or in_azure_function()
        self._trace_compute_stats = asbool(
            os.getenv(
                "DD_TRACE_COMPUTE_STATS", os.getenv("DD_TRACE_STATS_COMPUTATION_ENABLED", trace_compute_stats_default)
            )
        )
        self._data_streams_enabled = asbool(os.getenv("DD_DATA_STREAMS_ENABLED", False))

        dd_trace_obfuscation_query_string_regexp = os.getenv(
            "DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP", DD_TRACE_OBFUSCATION_QUERY_STRING_REGEXP_DEFAULT
        )
        self.global_query_string_obfuscation_disabled = True  # If empty obfuscation pattern
        self._obfuscation_query_string_pattern = None
        self.http_tag_query_string = True  # Default behaviour of query string tagging in http.url
        if dd_trace_obfuscation_query_string_regexp != "":
            self.global_query_string_obfuscation_disabled = False  # Not empty obfuscation pattern
            try:
                self._obfuscation_query_string_pattern = re.compile(
                    dd_trace_obfuscation_query_string_regexp.encode("ascii")
                )
            except Exception:
                log.warning("Invalid obfuscation pattern, disabling query string tracing", exc_info=True)
                self.http_tag_query_string = False  # Disable query string tagging if malformed obfuscation pattern

        self._ci_visibility_agentless_enabled = asbool(os.getenv("DD_CIVISIBILITY_AGENTLESS_ENABLED", default=False))
        self._ci_visibility_agentless_url = os.getenv("DD_CIVISIBILITY_AGENTLESS_URL", default="")
        self._ci_visibility_intelligent_testrunner_enabled = asbool(
            os.getenv("DD_CIVISIBILITY_ITR_ENABLED", default=True)
        )
        self.ci_visibility_log_level = os.getenv("DD_CIVISIBILITY_LOG_LEVEL", default="info")
        self._otel_enabled = asbool(os.getenv("DD_TRACE_OTEL_ENABLED", False))
        if self._otel_enabled:
            # Replaces the default otel api runtime context with DDRuntimeContext
            # https://github.com/open-telemetry/opentelemetry-python/blob/v1.16.0/opentelemetry-api/src/opentelemetry/context/__init__.py#L53
            os.environ["OTEL_PYTHON_CONTEXT"] = "ddcontextvars_context"
        self._ddtrace_bootstrapped = False
        self._subscriptions = []  # type: List[Tuple[List[str], Callable[[Config, List[str]], None]]]
        self._span_aggregator_rlock = asbool(os.getenv("DD_TRACE_SPAN_AGGREGATOR_RLOCK", True))

        self.trace_methods = os.getenv("DD_TRACE_METHODS")

        self._telemetry_install_id = os.getenv("DD_INSTRUMENTATION_INSTALL_ID", None)
        self._telemetry_install_type = os.getenv("DD_INSTRUMENTATION_INSTALL_TYPE", None)
        self._telemetry_install_time = os.getenv("DD_INSTRUMENTATION_INSTALL_TIME", None)

        self._dd_api_key = os.getenv("DD_API_KEY")
        self._dd_site = os.getenv("DD_SITE", "datadoghq.com")

        self._llmobs_enabled = asbool(os.getenv("DD_LLMOBS_ENABLED", False))
        self._llmobs_sample_rate = float(os.getenv("DD_LLMOBS_SAMPLE_RATE", 1.0))
        self._llmobs_ml_app = os.getenv("DD_LLMOBS_ML_APP")
        self._llmobs_agentless_enabled = asbool(os.getenv("DD_LLMOBS_AGENTLESS_ENABLED", False))

        self._inject_force = asbool(os.getenv("DD_INJECT_FORCE", False))
        self._lib_was_injected = False
        self._inject_was_attempted = asbool(os.getenv("_DD_INJECT_WAS_ATTEMPTED", False))

    def __getattr__(self, name) -> Any:
        if name in self._config:
            return self._config[name].value()
        if name not in self._integration_configs:
            self._integration_configs[name] = IntegrationConfig(self, name)

        return self._integration_configs[name]

    def _add_extra_service(self, service_name: str) -> None:
        if self._extra_services_queue is None:
            return
        if service_name != self.service:
            self._extra_services_queue.put(service_name)

    def _get_extra_services(self):
        # type: () -> set[str]
        if self._extra_services_queue is None:
            return set()
        self._extra_services.update(self._extra_services_queue.get_all())
        while len(self._extra_services) > 64:
            self._extra_services.pop()
        return self._extra_services

    def get_from(self, obj):
        """Retrieves the configuration for the given object.
        Any object that has an attached `Pin` must have a configuration
        and if a wrong object is given, an empty `dict` is returned
        for safety reasons.
        """
        pin = Pin.get_from(obj)
        if pin is None:
            log.debug("No configuration found for %s", obj)
            return {}

        return pin._config

    def _add(self, integration, settings, merge=True):
        """Internal API that registers an integration with given default
        settings.

        :param str integration: The integration name (i.e. `requests`)
        :param dict settings: A dictionary that contains integration settings;
            to preserve immutability of these values, the dictionary is copied
            since it contains integration defaults.
        :param bool merge: Whether to merge any existing settings with those provided,
            or if we should overwrite the settings with those provided;
            Note: when merging existing settings take precedence.
        """
        # DEV: Use `getattr()` to call our `__getattr__` helper
        existing = getattr(self, integration)
        settings = deepcopy(settings)

        if merge:
            # DEV: This may appear backwards keeping `existing` as the "source" and `settings` as
            #   the "destination", but we do not want to let `_add(..., merge=True)` overwrite any
            #   existing settings
            #
            # >>> config.requests['split_by_domain'] = True
            # >>> config._add('requests', dict(split_by_domain=False))
            # >>> config.requests['split_by_domain']
            # True
            self._integration_configs[integration] = IntegrationConfig(
                self, integration, _deepmerge(existing, settings)
            )
        else:
            self._integration_configs[integration] = IntegrationConfig(self, integration, settings)

    def trace_headers(self, whitelist):
        """
        Registers a set of headers to be traced at global level or integration level.
        :param whitelist: the case-insensitive list of traced headers
        :type whitelist: list of str or str
        :return: self
        :rtype: HttpConfig
        """
        self.http.trace_headers(whitelist)
        return self

    def header_is_traced(self, header_name):
        # type: (str) -> bool
        """
        Returns whether or not the current header should be traced.
        :param header_name: the header name
        :type header_name: str
        :rtype: bool
        """
        return self.http.header_is_traced(header_name)

    @cachedmethod()
    def _header_tag_name(self, header_name):
        # type: (str) -> Optional[str]
        return self.http._header_tag_name(header_name)

    def _get_service(self, default=None):
        """
        Returns the globally configured service or the default if none is configured.

        :param default: the default service to use if none is configured or
            found.
        :type default: str
        :rtype: str|None
        """
        # TODO: This method can be replaced with `config.service`.
        return self.service if self.service is not None else default

    def __repr__(self):
        cls = self.__class__
        integrations = ", ".join(self._integration_config.keys())
        return "{}.{}({})".format(cls.__module__, cls.__name__, integrations)

    def _subscribe(self, items, handler):
        # type: (List[str], Callable[[Config, List[str]], None]) -> None
        self._subscriptions.append((items, handler))

    def _notify_subscribers(self, changed_items):
        # type: (List[str]) -> None
        for sub_items, sub_handler in self._subscriptions:
            sub_updated_items = [i for i in changed_items if i in sub_items]
            if sub_updated_items:
                sub_handler(self, sub_updated_items)

    def __setattr__(self, key, value):
        # type: (str, Any) -> None
        if key == "_config":
            return super(self.__class__, self).__setattr__(key, value)
        elif key in self._config:
            self._set_config_items([(key, value, "code")])
            return None
        else:
            return super(self.__class__, self).__setattr__(key, value)

    def _set_config_items(self, items):
        # type: (List[Tuple[str, Any, _ConfigSource]]) -> None
        item_names = []
        for key, value, origin in items:
            item_names.append(key)
            self._config[key].set_value_source(value, origin)
        if self._telemetry_enabled:
            from ..internal.telemetry import telemetry_writer

            telemetry_writer.add_configs_changed(item_names)
        self._notify_subscribers(item_names)

    def _reset(self):
        # type: () -> None
        self._config = _default_config()

    def _get_source(self, item):
        # type: (str) -> str
        return self._config[item].source()

    def _remoteconfigPubSub(self):
        from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
        from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
        from ddtrace.internal.remoteconfig._pubsub import PubSub
        from ddtrace.internal.remoteconfig._pubsub import RemoteConfigSubscriber

        class _GlobalConfigPubSub(PubSub):
            __publisher_class__ = RemoteConfigPublisher
            __subscriber_class__ = RemoteConfigSubscriber
            __shared_data__ = PublisherSubscriberConnector()

            def __init__(self, callback):
                self._publisher = self.__publisher_class__(self.__shared_data__, None)
                self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, "GlobalConfig")

        return _GlobalConfigPubSub

    def _handle_remoteconfig(self, data, test_tracer=None):
        # type: (Any, Any) -> None
        if not isinstance(data, dict) or (isinstance(data, dict) and "config" not in data):
            log.warning("unexpected RC payload %r", data)
            return
        if len(data["config"]) == 0:
            log.warning("unexpected number of RC payloads %r", data)
            return

        # Check if 'lib_config' is a key in the dictionary since other items can be sent in the payload
        config = None
        for config_item in data["config"]:
            if isinstance(config_item, Dict):
                if "lib_config" in config_item:
                    config = config_item
                    break

        # If no data is submitted then the RC config has been deleted. Revert the settings.
        base_rc_config = {n: None for n in self._config}

        if config and "lib_config" in config:
            lib_config = config["lib_config"]
            if "tracing_sampling_rate" in lib_config:
                base_rc_config["_trace_sample_rate"] = lib_config["tracing_sampling_rate"]

            if "tracing_sampling_rules" in lib_config:
                trace_sampling_rules = lib_config["tracing_sampling_rules"]
                if trace_sampling_rules:
                    # returns None if no rules
                    trace_sampling_rules = self.convert_rc_trace_sampling_rules(trace_sampling_rules)
                    if trace_sampling_rules:
                        base_rc_config["_trace_sampling_rules"] = trace_sampling_rules

            if "log_injection_enabled" in lib_config:
                base_rc_config["logs_injection"] = lib_config["log_injection_enabled"]

            if "tracing_tags" in lib_config:
                tags = lib_config["tracing_tags"]
                if tags:
                    tags = self._format_tags(lib_config["tracing_tags"])
                base_rc_config["tags"] = tags

            if "tracing_enabled" in lib_config and lib_config["tracing_enabled"] is not None:
                base_rc_config["_tracing_enabled"] = asbool(lib_config["tracing_enabled"])  # type: ignore[assignment]

            if "tracing_header_tags" in lib_config:
                tags = lib_config["tracing_header_tags"]
                if tags:
                    tags = self._format_tags(lib_config["tracing_header_tags"])
                base_rc_config["trace_http_header_tags"] = tags
        self._set_config_items([(k, v, "remote_config") for k, v in base_rc_config.items()])
        # called unconditionally to handle the case where header tags have been unset
        self._handle_remoteconfig_header_tags(base_rc_config)

    def _handle_remoteconfig_header_tags(self, base_rc_config):
        """Implements precedence order between remoteconfig header tags from code, env, and RC"""
        header_tags_conf = self._config["trace_http_header_tags"]
        env_headers = header_tags_conf._env_value or {}
        code_headers = header_tags_conf._code_value or {}
        non_rc_header_tags = {**code_headers, **env_headers}
        selected_header_tags = base_rc_config.get("trace_http_header_tags") or non_rc_header_tags
        self.http = HttpConfig(header_tags=selected_header_tags)

    def _format_tags(self, tags: List[Union[str, Dict]]) -> Dict[str, str]:
        if not tags:
            return {}
        if isinstance(tags[0], Dict):
            pairs = [(item["header"], item["tag_name"]) for item in tags]  # type: ignore[index]
        else:
            pairs = [t.split(":") for t in tags]  # type: ignore[union-attr,misc]
        return {k: v for k, v in pairs}

    def enable_remote_configuration(self):
        # type: () -> None
        """Enable fetching configuration from Datadog."""
        from ddtrace.internal.flare.flare import Flare
        from ddtrace.internal.flare.handler import _handle_tracer_flare
        from ddtrace.internal.flare.handler import _tracerFlarePubSub
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        remoteconfig_pubsub = self._remoteconfigPubSub()(self._handle_remoteconfig)
        flare = Flare(trace_agent_url=self._trace_agent_url, api_key=self._dd_api_key, ddconfig=self.__dict__)
        tracerflare_pubsub = _tracerFlarePubSub()(_handle_tracer_flare, flare)
        remoteconfig_poller.register("APM_TRACING", remoteconfig_pubsub)
        remoteconfig_poller.register("AGENT_CONFIG", tracerflare_pubsub)
        remoteconfig_poller.register("AGENT_TASK", tracerflare_pubsub)

    def _remove_invalid_rules(self, rc_rules: List) -> List:
        """Remove invalid sampling rules from the given list"""
        # loop through list of dictionaries, if a dictionary doesn't have certain attributes, remove it
        for rule in rc_rules:
            if (
                ("service" not in rule and "name" not in rule and "resource" not in rule and "tags" not in rule)
                or "sample_rate" not in rule
                or "provenance" not in rule
            ):
                log.debug("Invalid sampling rule from remoteconfig found, rule will be removed: %s", rule)
                rc_rules.remove(rule)

        return rc_rules

    def _tags_to_dict(self, tags: List[Dict]):
        """
        Converts a list of tag dictionaries to a single dictionary.
        """
        if isinstance(tags, list):
            return {tag["key"]: tag["value_glob"] for tag in tags}
        return tags

    def convert_rc_trace_sampling_rules(self, rc_rules: List[Dict[str, Any]]) -> Optional[str]:
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
        rc_rules = self._remove_invalid_rules(rc_rules)
        for rule in rc_rules:
            tags = rule.get("tags")
            if tags:
                rule["tags"] = self._tags_to_dict(tags)
        if rc_rules:
            return json.dumps(rc_rules)
        else:
            return None
