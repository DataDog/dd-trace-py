from copy import deepcopy
import os
import re
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.constants import APPSEC_ENV
from ddtrace.constants import IAST_ENV
from ddtrace.internal.utils.cache import cachedmethod

from ..internal.constants import PROPAGATION_STYLE_ALL
from ..internal.constants import PROPAGATION_STYLE_DATADOG
from ..internal.logger import get_logger
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


def _parse_propagation_styles(name, default):
    # type: (str, str) -> set[str]
    """Helper to parse http propagation extract/inject styles via env variables.

    The expected format is::

        <style>[,<style>...]


    The allowed values are:

    - "datadog"
    - "b3"
    - "b3 single header"


    The default value is ``"datadog"``.


    Examples::

        # Extract trace context from "x-datadog-*" or "x-b3-*" headers from upstream headers
        DD_TRACE_PROPAGATION_STYLE_EXTRACT="datadog,b3"

        # Inject the "b3: *" header into downstream requests headers
        DD_TRACE_PROPAGATION_STYLE_INJECT="b3 single header"
    """
    styles = set()
    envvar = os.getenv(name, default=default)
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
        styles.add(style)
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

        header_tags = parse_tags_str(os.getenv("DD_TRACE_HEADER_TAGS", ""))
        self.http = HttpConfig(header_tags=header_tags)

        # Master switch for turning on and off trace search by default
        # this weird invocation of getenv is meant to read the DD_ANALYTICS_ENABLED
        # legacy environment variable. It should be removed in the future
        legacy_config_value = os.getenv("DD_ANALYTICS_ENABLED", default=False)

        self.analytics_enabled = asbool(os.getenv("DD_TRACE_ANALYTICS_ENABLED", default=legacy_config_value))

        self.tags = parse_tags_str(os.getenv("DD_TAGS") or "")

        self.env = os.getenv("DD_ENV") or self.tags.get("env")
        self.service = os.getenv("DD_SERVICE", default=self.tags.get("service"))
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

        # Propagation styles
        self._propagation_style_extract = _parse_propagation_styles(
            "DD_TRACE_PROPAGATION_STYLE_EXTRACT", default=PROPAGATION_STYLE_DATADOG
        )
        self._propagation_style_inject = _parse_propagation_styles(
            "DD_TRACE_PROPAGATION_STYLE_INJECT", default=PROPAGATION_STYLE_DATADOG
        )

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
            os.getenv("DD_TRACE_COMPUTE_STATS", os.getenv("DD_TRACE_STATS_COMPUTATION_ENABLED", False))
        )
        self._appsec_enabled = asbool(os.getenv(APPSEC_ENV, False))
        self._iast_enabled = asbool(os.getenv(IAST_ENV, False))

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
