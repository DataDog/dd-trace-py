from copy import deepcopy
import os
import re
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.appsec._constants import API_SECURITY
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import DEFAULT
from ddtrace.constants import APPSEC_ENV
from ddtrace.constants import IAST_ENV
from ddtrace.internal.serverless import in_azure_function_consumption_plan
from ddtrace.internal.serverless import in_gcp_function
from ddtrace.internal.utils.cache import cachedmethod

from ..internal import gitmetadata
from ..internal.constants import DEFAULT_BUFFER_SIZE
from ..internal.constants import DEFAULT_MAX_PAYLOAD_SIZE
from ..internal.constants import DEFAULT_PROCESSING_INTERVAL
from ..internal.constants import DEFAULT_REUSE_CONNECTIONS
from ..internal.constants import DEFAULT_SAMPLING_RATE_LIMIT
from ..internal.constants import DEFAULT_TIMEOUT
from ..internal.constants import PROPAGATION_STYLE_ALL
from ..internal.constants import _PROPAGATION_STYLE_DEFAULT
from ..internal.logger import get_logger
from ..internal.schema import DEFAULT_SPAN_SERVICE_NAME
from ..internal.utils.formats import asbool
from ..internal.utils.formats import parse_tags_str
from ..pin import Pin
from .http import HttpConfig
from .integration import IntegrationConfig


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
    - "b3 single header"
    - "tracecontext"
    - "none"


    The default value is ``"tracecontext,datadog"``.


    Examples::

        # Extract and inject b3 headers:
        DD_TRACE_PROPAGATION_STYLE="b3multi"

        # Disable header propagation:
        DD_TRACE_PROPAGATION_STYLE="none"

        # Extract trace context from "x-datadog-*" or "x-b3-*" headers from upstream headers
        DD_TRACE_PROPAGATION_STYLE_EXTRACT="datadog,b3multi"

        # Inject the "b3: *" header into downstream requests headers
        DD_TRACE_PROPAGATION_STYLE_INJECT="b3 single header"
    """
    styles = []
    envvar = os.getenv(name, default=default)
    if envvar is None:
        return None
    for style in envvar.split(","):
        style = style.strip().lower()
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
        # use a dict as underlying storing mechanism
        self._config = {}

        self._debug_mode = asbool(os.getenv("DD_TRACE_DEBUG", default=False))
        self._startup_logs_enabled = asbool(os.getenv("DD_TRACE_STARTUP_LOGS", False))

        self._trace_sample_rate = os.getenv("DD_TRACE_SAMPLE_RATE")
        self._trace_rate_limit = int(os.getenv("DD_TRACE_RATE_LIMIT", default=DEFAULT_SAMPLING_RATE_LIMIT))
        self._trace_sampling_rules = os.getenv("DD_TRACE_SAMPLING_RULES")
        self._partial_flush_enabled = asbool(os.getenv("DD_TRACE_PARTIAL_FLUSH_ENABLED", default=True))
        self._partial_flush_min_spans = int(os.getenv("DD_TRACE_PARTIAL_FLUSH_MIN_SPANS", default=500))
        self._priority_sampling = asbool(os.getenv("DD_PRIORITY_SAMPLING", default=True))

        header_tags = parse_tags_str(os.getenv("DD_TRACE_HEADER_TAGS", ""))
        self.http = HttpConfig(header_tags=header_tags)
        self._tracing_enabled = asbool(os.getenv("DD_TRACE_ENABLED", default=True))
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

        # Master switch for turning on and off trace search by default
        # this weird invocation of getenv is meant to read the DD_ANALYTICS_ENABLED
        # legacy environment variable. It should be removed in the future
        legacy_config_value = os.getenv("DD_ANALYTICS_ENABLED", default=False)

        self.analytics_enabled = asbool(os.getenv("DD_TRACE_ANALYTICS_ENABLED", default=legacy_config_value))
        self.client_ip_header = os.getenv("DD_TRACE_CLIENT_IP_HEADER")
        self.retrieve_client_ip = asbool(os.getenv("DD_TRACE_CLIENT_IP_ENABLED", default=False))

        # cleanup DD_TAGS, because values will be inserted back in the optimal way (via _dd.git.* tags)
        self.tags = gitmetadata.clean_tags(parse_tags_str(os.getenv("DD_TAGS") or ""))

        self.env = os.getenv("DD_ENV") or self.tags.get("env")
        self.service = os.getenv("DD_SERVICE", default=self.tags.get("service", DEFAULT_SPAN_SERVICE_NAME))

        if self.service is None and in_gcp_function():
            self.service = os.environ.get("K_SERVICE", os.environ.get("FUNCTION_NAME"))

        if self.service is None and in_azure_function_consumption_plan():
            self.service = os.environ.get("WEBSITE_SITE_NAME")

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

        self.logs_injection = asbool(os.getenv("DD_LOGS_INJECTION", default=False))

        self.report_hostname = asbool(os.getenv("DD_TRACE_REPORT_HOSTNAME", default=False))

        self.health_metrics_enabled = asbool(os.getenv("DD_TRACE_HEALTH_METRICS_ENABLED", default=False))

        self._telemetry_enabled = asbool(os.getenv("DD_INSTRUMENTATION_TELEMETRY_ENABLED", True))
        self._telemetry_heartbeat_interval = float(os.getenv("DD_TELEMETRY_HEARTBEAT_INTERVAL", "60"))

        self._runtime_metrics_enabled = asbool(os.getenv("DD_RUNTIME_METRICS_ENABLED", False))

        self._128_bit_trace_id_enabled = asbool(os.getenv("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", False))

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

        # Datadog tracer tags propagation
        x_datadog_tags_max_length = int(os.getenv("DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH", default=512))
        if x_datadog_tags_max_length < 0 or x_datadog_tags_max_length > 512:
            raise ValueError(
                (
                    "Invalid value {!r} provided for DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH, "
                    "only non-negative values less than or equal to 512 allowed"
                ).format(x_datadog_tags_max_length)
            )
        self._x_datadog_tags_max_length = x_datadog_tags_max_length
        self._x_datadog_tags_enabled = x_datadog_tags_max_length > 0

        # Raise certain errors only if in testing raise mode to prevent crashing in production with non-critical errors
        self._raise = asbool(os.getenv("DD_TESTING_RAISE", False))

        trace_compute_stats_default = in_gcp_function() or in_azure_function_consumption_plan()
        self._trace_compute_stats = asbool(
            os.getenv(
                "DD_TRACE_COMPUTE_STATS", os.getenv("DD_TRACE_STATS_COMPUTATION_ENABLED", trace_compute_stats_default)
            )
        )
        self._data_streams_enabled = asbool(os.getenv("DD_DATA_STREAMS_ENABLED", False))
        self._appsec_enabled = asbool(os.getenv(APPSEC_ENV, False))
        self._automatic_login_events_mode = os.getenv(APPSEC.AUTOMATIC_USER_EVENTS_TRACKING, "safe")
        self._user_model_login_field = os.getenv(APPSEC.USER_MODEL_LOGIN_FIELD, default="")
        self._user_model_email_field = os.getenv(APPSEC.USER_MODEL_EMAIL_FIELD, default="")
        self._user_model_name_field = os.getenv(APPSEC.USER_MODEL_NAME_FIELD, default="")
        self._iast_enabled = asbool(os.getenv(IAST_ENV, False))
        self._api_security_enabled = asbool(os.getenv(API_SECURITY.ENV_VAR_ENABLED, False))
        self._waf_timeout = DEFAULT.WAF_TIMEOUT
        try:
            self._waf_timeout = float(os.getenv("DD_APPSEC_WAF_TIMEOUT"))
        except (TypeError, ValueError):
            pass

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
            os.getenv("DD_CIVISIBILITY_ITR_ENABLED", default=False)
        )
        self._ci_visibility_unittest_enabled = asbool(os.getenv("DD_CIVISIBILITY_UNITTEST_ENABLED", default=False))
        self._otel_enabled = asbool(os.getenv("DD_TRACE_OTEL_ENABLED", False))
        if self._otel_enabled:
            # Replaces the default otel api runtime context with DDRuntimeContext
            # https://github.com/open-telemetry/opentelemetry-python/blob/v1.16.0/opentelemetry-api/src/opentelemetry/context/__init__.py#L53
            os.environ["OTEL_PYTHON_CONTEXT"] = "ddcontextvars_context"
        self._ddtrace_bootstrapped = False
        self._span_aggregator_rlock = asbool(os.getenv("DD_TRACE_SPAN_AGGREGATOR_RLOCK", True))

        self._iast_redaction_enabled = asbool(os.getenv("DD_IAST_REDACTION_ENABLED", default=True))
        self._iast_redaction_name_pattern = os.getenv(
            "DD_IAST_REDACTION_NAME_PATTERN",
            default=r"(?i)^.*(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|private_?|"
            + r"public_?|access_?|secret_?)key(?:_?id)?|token|consumer_?(?:id|key|secret)|"
            + r"sign(?:ed|ature)?|auth(?:entication|orization)?)",
        )
        self._iast_redaction_value_pattern = os.getenv(
            "DD_IAST_REDACTION_VALUE_PATTERN",
            default=r"(?i)bearer\s+[a-z0-9\._\-]+|token:[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|"
            + r"ey[I-L][\w=-]+\.ey[I-L][\w=-]+(\.[\w.+\/=-]+)?|[\-]{5}BEGIN[a-z\s]+PRIVATE\sKEY"
            + r"[\-]{5}[^\-]+[\-]{5}END[a-z\s]+PRIVATE\sKEY|ssh-rsa\s*[a-z0-9\/\.+]{100,}",
        )

    def __getattr__(self, name):
        if name not in self._config:
            self._config[name] = IntegrationConfig(self, name)

        return self._config[name]

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
            self._config[integration] = IntegrationConfig(self, integration, _deepmerge(existing, settings))
        else:
            self._config[integration] = IntegrationConfig(self, integration, settings)

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
        integrations = ", ".join(self._config.keys())
        return "{}.{}({})".format(cls.__module__, cls.__name__, integrations)
