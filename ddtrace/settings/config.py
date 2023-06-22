from copy import deepcopy
import os
import re
import typing
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from typing_extensions import TypedDict

from ddtrace.appsec._constants import DEFAULT
from ddtrace.constants import APPSEC_ENV
from ddtrace.constants import IAST_ENV
from ddtrace.internal.serverless import in_gcp_function
from ddtrace.internal.utils.cache import cachedmethod
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from ..internal import gitmetadata
from ..internal.constants import PROPAGATION_STYLE_ALL
from ..internal.constants import PROPAGATION_STYLE_B3
from ..internal.constants import _PROPAGATION_STYLE_DEFAULT
from ..internal.logger import get_logger
from ..internal.schema import DEFAULT_SPAN_SERVICE_NAME
from ..internal.utils.formats import asbool
from ..internal.utils.formats import parse_tags_str
from ..pin import Pin
from .http import HttpConfig
from .integration import IntegrationConfig


log = get_logger(__name__)


DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN_DEFAULT = (
    r"(?i)(?:p(?:ass)?w(?:or)?d|pass(?:_?phrase)?|secret|(?:api_?|"
    r"private_?|public_?|access_?|secret_?)key(?:_?id)?|token|consumer_?(?:id|key|secret)|"
    r'sign(?:ed|ature)?|auth(?:entication|orization)?)(?:(?:\s|%20)*(?:=|%3D)[^&]+|(?:"|%22)'
    r'(?:\s|%20)*(?::|%3A)(?:\s|%20)*(?:"|%22)(?:%2[^2]|%[^2]|[^"%])+(?:"|%22))|bearer(?:\s|%20)'
    r"+[a-z0-9\._\-]|token(?::|%3A)[a-z0-9]{13}|gh[opsu]_[0-9a-zA-Z]{36}|ey[I-L](?:[\w=-]|%3D)+\.ey[I-L]"
    r"(?:[\w=-]|%3D)+(?:\.(?:[\w.+\/=-]|%3D|%2F|%2B)+)?|[\-]{5}BEGIN(?:[a-z\s]|%20)+"
    r"PRIVATE(?:\s|%20)KEY[\-]{5}[^\-]+[\-]{5}END(?:[a-z\s]|%20)+PRIVATE(?:\s|%20)KEY|"
    r"ssh-rsa(?:\s|%20)*(?:[a-z0-9\/\.+]|%2F|%5C|%2B){100,}"
)


def _serverless_service(s):
    if not in_gcp_function():
        return None
    return s


def _service_from_tags(s):
    # type: (str) -> Optional[str]
    tags = parse_tags_str(s)
    return tags.get("service")


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
        if style == "b3":
            deprecate(
                'Using DD_TRACE_PROPAGATION_STYLE="b3" is deprecated',
                message="Please use 'DD_TRACE_PROPAGATION_STYLE=\"b3multi\"' instead",
                removal_version="2.0.0",
                category=DDTraceDeprecationWarning,
            )
            style = PROPAGATION_STYLE_B3
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


_ConfigResult = TypedDict("_ConfigResult", {"value": Any, "raw": Any, "source": Any})


class _ConfigSource(object):
    """A configuration source that can be queried for a value."""

    def get(self):
        # type: () -> _ConfigResult
        raise NotImplementedError


class _ConfigSourceEnv(_ConfigSource):
    """A configuration source that reads from an environment variable."""

    def __init__(
        self,
        name,  # type: str
        factory=lambda x: x,  # type: Callable[[str], Any]
        examples=None,  # type: Optional[List[str]]
    ):
        # type: (...) -> None
        self.key = name
        self.factory = factory
        self.examples = examples or []

    def get(self):
        # type: () -> _ConfigResult
        raw = os.environ.get(self.key)
        value = None
        if raw is not None:
            value = self.factory(raw)
        return {"value": value, "raw": raw, "source": "env_var"}


class _ConfigSourceEnvNone(_ConfigSourceEnv):
    def __init__(self, *args, **kwargs):
        # type: (*Any, **Any) -> None
        pass

    def get(self):
        # type: () -> _ConfigResult
        return {"value": None, "raw": None, "source": "env_var"}


class _ConfigSourceEnvMulti(_ConfigSourceEnv):
    def __init__(self, *envs, **kwargs):
        # type: (*_ConfigSourceEnv, **Any) -> None
        resolver = typing.cast("Optional[Callable[[_ConfigSourceEnv], _ConfigResult]]", kwargs.get("resolver"))
        self.envs = envs
        self.resolver = resolver or self._default_resolver

    @staticmethod
    def _default_resolver(*envs):
        # type: (_ConfigSourceEnv) -> _ConfigResult
        """
        Default is to take the first env var that has a value.
        """
        for e in envs:
            v = e.get()
            if v["value"] is not None:
                return v
        return {
            "value": None,
            "raw": None,
            "source": "env_var",
        }

    def get(self):
        # type: () -> _ConfigResult
        return self.resolver(*self.envs)


class _ConfigSourceProgrammatic(_ConfigSource):
    """A configuration source that is set programmatically by the user."""

    def __init__(
        self,
        factory=None,  # type: Optional[Callable[[Any], Any]]
    ):
        # type: (...) -> None
        self._value = None
        self._raw = None
        self._factory = factory

    def set(self, val):
        # type: (Any) -> None
        self._raw = val
        self._value = val
        if val is not None and self._factory is not None:
            self._value = self._factory(val)

    def get(self):
        # type: () -> _ConfigResult
        return {
            "value": self._value,
            "raw": self._raw,
            "source": "code",
        }


class _ConfigItem(object):
    """A configuration item that can be queried for a value.

    By default, the value is resolved in the following order:
        1. programmatic
        2. environ
        3. default
    """

    _sentinel = object()

    def __init__(
        self,
        key,  # type: str
        type_,  # type: str
        default,  # type: Any
        environ=None,  # type: Optional[_ConfigSourceEnv]
        programmatic=None,  # type: Optional[_ConfigSourceProgrammatic]
        resolve=None,  # type: Optional[Callable[[Any, _ConfigSourceEnv, _ConfigSourceProgrammatic], _ConfigResult]]
        metadata=None,  # type: Optional[Dict[str, Any]]
    ):
        # type: (...) -> None
        self.key = key
        self.type = type_
        self._default_factory = default if callable(default) else lambda: default
        self.environ = environ or _ConfigSourceEnvNone()  # type: _ConfigSourceEnv
        self.programmatic = programmatic or _ConfigSourceProgrammatic()  # type: _ConfigSourceProgrammatic
        self.resolve = self._default_resolve if resolve is None else resolve
        self.metadata = metadata or {
            "description": "",
            "version_added": "",
        }
        self._cached_value = self._sentinel

    @property
    def default(self):
        # type: () -> Any
        """Return the default value for this configuration item.

        A method is used to avoid mutable default values like ``{}``.
        """
        return self._default_factory()

    @staticmethod
    def _default_resolve(default, environ, programmatic):
        # type: (Any, _ConfigSourceEnv, _ConfigSourceProgrammatic) -> _ConfigResult
        for source in (programmatic, environ):
            v = source.get()
            if v["value"] is not None:
                return v
        return {
            "value": default,
            "raw": default,
            "source": "default",
        }

    def _resolve(self):
        if self._cached_value is self._sentinel:
            self._cached_value = self.resolve(
                self.default,
                self.environ,
                self.programmatic,
            )
        return self._cached_value

    @property
    def value(self):
        # type: () -> Any
        return self._resolve()["value"]

    @property
    def resolved(self):
        # type: () -> _ConfigResult
        return self._resolve()

    def set_user_value(self, val):
        # type: (Any) -> None
        """Set the value the user specifies programmatically."""
        self.programmatic.set(val)
        self._cached_value = self._sentinel

    def reset(self):
        # type: () -> None
        """Reset the item by removing any user specified values.

        Also invalidate the cache to recompute the resolved value.
        """
        self.programmatic.set(None)
        self._cached_value = self._sentinel


def _default_config():
    # type: () -> Tuple[_ConfigItem]
    return (
        _ConfigItem(
            key="service",
            type_="Optional[str]",
            default=DEFAULT_SPAN_SERVICE_NAME,
            environ=_ConfigSourceEnvMulti(
                _ConfigSourceEnv(name="DD_SERVICE", examples=["my-web-service"]),
                _ConfigSourceEnv(
                    name="DD_TAGS",
                    factory=_service_from_tags,
                    examples=["service:my-web-service", "service:my-web-service,env:prod"],
                ),
                # Serverless environments
                _ConfigSourceEnv(name="FUNCTION_NAME", factory=_serverless_service),
                _ConfigSourceEnv(name="K_SERVICE", factory=_serverless_service),
            ),
            metadata={
                "description": (
                    "Service name to be used for the application. "
                    "This is the primary key used in the Datadog product for data submitted from this library. "
                    "See `Unified Service Tagging`_ for more information."
                ),
                "version_added": {
                    "v0.36.0": "",
                },
            },
        ),
    )


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

    def __init__(self, *cfg_items):
        # Backwards compatibility: Config used to not accept any arguments
        # when none are provided, use the default config items.
        cfg_items = cfg_items or _default_config()

        # Must come before _integration_configs due to __setattr__
        self._items = {}
        for cfg in cfg_items:
            self._items[cfg.key] = cfg

        # use a dict as underlying storing mechanism for integration configs
        self._integration_configs = {}

        header_tags = parse_tags_str(os.getenv("DD_TRACE_HEADER_TAGS", ""))
        self.http = HttpConfig(header_tags=header_tags)

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

        self.version = os.getenv("DD_VERSION", default=self.tags.get("version"))
        self.http_server = self._HTTPServerConfig()

        self.service_mapping = parse_tags_str(os.getenv("DD_SERVICE_MAPPING", default=""))

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

        self._telemetry_metrics_enabled = asbool(os.getenv("DD_TELEMETRY_METRICS_ENABLED", default=True))

        self._128_bit_trace_id_enabled = asbool(os.getenv("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", False))

        self._128_bit_trace_id_logging_enabled = asbool(os.getenv("DD_TRACE_128_BIT_TRACEID_LOGGING_ENABLED", False))

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
        self._trace_compute_stats = asbool(
            os.getenv("DD_TRACE_COMPUTE_STATS", os.getenv("DD_TRACE_STATS_COMPUTATION_ENABLED", in_gcp_function()))
        )
        self._data_streams_enabled = asbool(os.getenv("DD_DATA_STREAMS_ENABLED", False))
        self._appsec_enabled = asbool(os.getenv(APPSEC_ENV, False))
        self._iast_enabled = asbool(os.getenv(IAST_ENV, False))
        self._waf_timeout = DEFAULT.WAF_TIMEOUT
        try:
            self._waf_timeout = float(os.getenv("DD_APPSEC_WAF_TIMEOUT"))
        except (TypeError, ValueError):
            pass

        dd_trace_obfuscation_query_string_pattern = os.getenv(
            "DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN", DD_TRACE_OBFUSCATION_QUERY_STRING_PATTERN_DEFAULT
        )
        self.global_query_string_obfuscation_disabled = True  # If empty obfuscation pattern
        self._obfuscation_query_string_pattern = None
        self.http_tag_query_string = True  # Default behaviour of query string tagging in http.url
        if dd_trace_obfuscation_query_string_pattern != "":
            self.global_query_string_obfuscation_disabled = False  # Not empty obfuscation pattern
            try:
                self._obfuscation_query_string_pattern = re.compile(
                    dd_trace_obfuscation_query_string_pattern.encode("ascii")
                )
            except Exception:
                log.warning("Invalid obfuscation pattern, disabling query string tracing")
                self.http_tag_query_string = False  # Disable query string tagging if malformed obfuscation pattern

        self._ci_visibility_agentless_enabled = asbool(os.getenv("DD_CIVISIBILITY_AGENTLESS_ENABLED", default=False))
        self._ci_visibility_agentless_url = os.getenv("DD_CIVISIBILITY_AGENTLESS_URL", default="")
        self._ci_visibility_intelligent_testrunner_enabled = asbool(
            os.getenv("DD_CIVISIBILITY_ITR_ENABLED", default=False)
        )
        self._ci_visibility_code_coverage_enabled = asbool(
            os.getenv("DD_CIVISIBILITY_CODE_COVERAGE_ENABLED", default=False)
        )
        self._otel_enabled = asbool(os.getenv("DD_TRACE_OTEL_ENABLED", False))
        if self._otel_enabled:
            # Replaces the default otel api runtime context with DDRuntimeContext
            # https://github.com/open-telemetry/opentelemetry-python/blob/v1.16.0/opentelemetry-api/src/opentelemetry/context/__init__.py#L53
            os.environ["OTEL_PYTHON_CONTEXT"] = "ddcontextvars_context"
        self._ddtrace_bootstrapped = False
        self._subscriptions = []  # type: List[Tuple[List[str], Callable[[Config, List[str]], None]]]

    def __getattr__(self, name):
        if name in self._items:
            return self._items[name].value

        if name not in self._integration_configs:
            self._integration_configs[name] = IntegrationConfig(self, name)

        return self._integration_configs[name]

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
        if key == "_items":
            return super(self.__class__, self).__setattr__(key, value)
        elif key in self._items:
            self._items[key].set_user_value(value)
            self._notify_subscribers([key])
            return None
        else:
            return super(self.__class__, self).__setattr__(key, value)

    def reset(self):
        # type: () -> None
        for cfg_item in self._items.values():
            cfg_item.reset()
