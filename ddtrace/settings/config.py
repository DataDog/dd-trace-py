from copy import deepcopy
import glob
import importlib
import os
import sys
from typing import List
from typing import Optional
from typing import Tuple

from envier import En

from ddtrace.internal import forksafe
from ddtrace.internal.utils.cache import cachedmethod

from ..internal.constants import PROPAGATION_STYLE_ALL
from ..internal.constants import PROPAGATION_STYLE_DATADOG
from ..internal.logger import get_logger
from ..internal.utils.formats import parse_tags_str
from ..pin import Pin
from .http import HttpConfig
from .integration import IntegrationConfig


log = get_logger(__name__)


def _parse_propagation_styles(envvar):
    # type: (str) -> set[str]
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
    for style in envvar.split(","):
        style = style.strip().lower()
        if not style:
            continue
        if style not in PROPAGATION_STYLE_ALL:
            raise ValueError(
                "Unknown propagation style {!r} provided, allowed values are {!r}".format(style, PROPAGATION_STYLE_ALL)
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


def _validate_x_datadog_tags_max_len(value):
    # type: (int) -> None
    if not (0 <= value <= 512):
        raise ValueError(
            (
                "Invalid value {!r} provided for DD_TRACE_X_DATADOG_TAGS_MAX_LENGTH, "
                "only non-negative values less than or equal to 512 allowed"
            ).format(value)
        )


class Config(En):
    """Configuration object that exposes an API to set and retrieve
    global settings for each integration. All integrations must use
    this instance to register their defaults, so that they're public
    available and can be updated by users.
    """

    __prefix__ = "dd"

    _int_config_lock = forksafe.Lock()

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

    header_tags = En.v(dict, "trace.header.tags", parser=parse_tags_str, default={})

    # Master switch for turning on and off trace search by default
    # this weird invocation of getenv is meant to read the DD_ANALYTICS_ENABLED
    # legacy environment variable. It should be removed in the future
    analytics_enabled = En.v(
        bool, "trace.analytics.enabled", default=False, deprecations=[("analytics.enabled", None, None)]
    )

    http = En.d(HttpConfig, lambda c: HttpConfig(header_tags=c.header_tags))
    tags = En.v(dict, "tags", parser=parse_tags_str, default={})

    _env = En.v(Optional[str], "env", default=None)
    env = En.d(Optional[str], lambda c: c._env or c.tags.get("env"))

    _service = En.v(Optional[str], "service", default=None)
    service = En.d(Optional[str], lambda c: c._service or c.tags.pop("service", None))

    _version = En.v(Optional[str], "version", default=None)
    version = En.d(Optional[str], lambda c: c._version or c.tags.pop("version", None))

    http_server = En.d(_HTTPServerConfig, lambda c: c._HTTPServerConfig())

    service_mapping = En.v(dict, "service.mapping", parser=parse_tags_str, default={})

    logs_injection = En.v(bool, "logs_injection", default=False)

    report_hostname = En.v(bool, "trace.report_hostname", default=False)

    health_metrics_enabled = En.v(bool, "trace.health_metrics.enabled", default=False)

    _propagation_style_extract = En.v(
        set, "trace.propagation_style.extract", parser=_parse_propagation_styles, default={PROPAGATION_STYLE_DATADOG}
    )
    _propagation_style_inject = En.v(
        set, "trace.propagation_style.inject", parser=_parse_propagation_styles, default={PROPAGATION_STYLE_DATADOG}
    )

    _x_datadog_tags_max_length = En.v(
        int, "trace.x_datadog_tags.max_length", validator=_validate_x_datadog_tags_max_len, default=512
    )
    _x_datadog_tags_enabled = En.d(bool, lambda c: c._x_datadog_tags_max_length > 0)

    _raise = En.v(bool, "testing.raise", default=False)
    _trace_compute_stats = En.v(bool, "trace.compute_stats", default=False)

    def __getattr__(self, name):
        with self._int_config_lock:
            # Some other thread might have created it while we were waiting to
            # acquire the lock, so we check again.
            int_config = self.__dict__.get(name, None)
            if int_config is not None:
                return int_config

            int_config = IntegrationConfig(self, name)
            setattr(self, name, int_config)
            return int_config

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
            setattr(self, integration, IntegrationConfig(self, integration, _deepmerge(existing, settings)))
        else:
            setattr(self, integration, IntegrationConfig(self, integration, settings))

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
        integrations = ", ".join(self.keys())
        return "{}.{}({})".format(cls.__module__, cls.__name__, integrations)


# Include extra configuration to the global one by introspecting the content of
# this submodule
for f in glob.glob(os.path.join(os.path.abspath(os.path.dirname(__file__)), "*.py")):
    if f.endswith("__init__.py") or f == os.path.abspath(__file__).replace(".pyc", ".py"):
        # We don't want to re-import the `__init__` module, nor the current
        # one (which might be loading as a pyc file).
        continue

    name, _ = os.path.splitext(os.path.basename(f))
    module_name = "ddtrace.settings.%s" % name

    module = importlib.import_module(module_name)

    try:
        (subconfig,) = (
            _ for _ in module.__dict__.values() if isinstance(_, type) and issubclass(_, En) and _ is not En
        )
    except ValueError:
        # The module doesn't have a top-level En subclass, or it has too many
        # and therefore it is invalid
        continue

    # Add the subconfig to the global config under the namespace given by the
    # module name
    Config.include(subconfig, namespace=name)

    # We can unload the module to free up resources
    del sys.modules[module_name]
