from copy import deepcopy
import glob
import importlib
import json
import os
import sys
from textwrap import dedent
from typing import List
from typing import Optional
from typing import Tuple

from envier import En
from envier import validators as v

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


def _parse_rules(rules):
    # type: (str) -> list
    from ddtrace.sampler import SamplingRule  # to avoid circular import

    sampling_rules = []
    if rules is not None:
        json_rules = []
        try:
            json_rules = json.loads(rules)
        except json.JSONDecodeError:
            raise ValueError("Unable to parse DD_TRACE_SAMPLING_RULES={}".format(rules))
        for rule in json_rules:
            if "sample_rate" not in rule:
                raise KeyError("No sample_rate provided for sampling rule: {}".format(json.dumps(rule)))
            sample_rate = float(rule["sample_rate"])
            service = rule.get("service", SamplingRule.NO_RULE)
            name = rule.get("name", SamplingRule.NO_RULE)
            try:
                sampling_rule = SamplingRule(sample_rate=sample_rate, service=service, name=name)
            except ValueError as e:
                raise ValueError("Error creating sampling rule {}: {}".format(json.dumps(rule), e))
            sampling_rules.append(sampling_rule)
    return sampling_rules


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

    header_tags = En.v(
        dict,
        "trace.header.tags",
        parser=parse_tags_str,
        default={},
        help_type="Mapping",
        help_default="",
        help="A map of case-insensitive header keys to tag names. Automatically applies matching header values as "
        "tags on root spans. For example, ``User-Agent:http.useragent,content-type:http.content_type``.",
    )

    # Master switch for turning on and off trace search by default
    # this weird invocation of getenv is meant to read the DD_ANALYTICS_ENABLED
    # legacy environment variable. It should be removed in the future
    analytics_enabled = En.v(
        bool, "trace.analytics.enabled", default=False, deprecations=[("analytics.enabled", None, None)]
    )

    http = En.d(HttpConfig, lambda c: HttpConfig(header_tags=c.header_tags))

    tags = En.v(
        dict,
        "tags",
        parser=parse_tags_str,
        default={},
        help_type="Mapping",
        help_default="",
        help="Set global tags to be attached to every span. Value must be either comma or space separated. "
        "e.g. ``key1:value1,key2,value2`` or ``key1:value key2:value2``. Comma separated support added in "
        "``v0.38.0`` and space separated support added in ``v0.48.0``.",
    )

    _env = En.v(
        Optional[str],
        "env",
        default=None,
        help_type="String",
        help="Set an application's environment e.g. ``prod``, ``pre-prod``, ``staging``. "
        "Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.",
    )
    env = En.d(Optional[str], lambda c: c._env or c.tags.get("env"))

    _service = En.v(
        Optional[str],
        "service",
        default=None,
        help_type="String",
        help_default="(autodetected)",
        help="Set the service name to be used for this application. A default is "
        "provided for these integrations: :ref:`bottle`, :ref:`flask`, :ref:`grpc`, "
        ":ref:`pyramid`, :ref:`pylons`, :ref:`tornado`, :ref:`celery`, :ref:`django` and "
        ":ref:`falcon`. Added in ``v0.36.0``. See `Unified Service Tagging`_ for more information.",
    )
    service = En.d(Optional[str], lambda c: c._service or c.tags.pop("service", None))

    _version = En.v(
        Optional[str],
        "version",
        default=None,
        help_type="String",
        help="Set an application's version in traces and logs e.g. ``1.2.3``, ``6c44da20``, ``2020.02.13``. "
        "Generally set along with ``DD_SERVICE``. Added in ``v0.36.0``. See `Unified Service Tagging`_ for "
        "more information.",
    )
    version = En.d(Optional[str], lambda c: c._version or c.tags.pop("version", None))

    site = En.v(
        str,
        "site",
        default="datadoghq.com",
        help_type="String",
        help="Specify which site to use for uploading profiles. Set to ``datadoghq.eu`` to use EU site.",
    )

    http_server = En.d(_HTTPServerConfig, lambda c: c._HTTPServerConfig())

    service_mapping = En.v(
        dict,
        "service.mapping",
        parser=parse_tags_str,
        default={},
        help_type="Mapping",
        help_default="",
        help="Define service name mappings to allow renaming services in traces, "
        "e.g. ``postgres:postgresql,defaultdb:postgresql``.",
    )

    logs_injection = En.v(
        bool,
        "logs_injection",
        default=False,
        help_type="Boolean",
        help="Enables :ref:`Logs Injection`",
    )

    call_basic_config = En.v(
        bool,
        "call_basic_config",
        default=False,
        help_type="Boolean",
        help="Controls whether ``logging.basicConfig`` is called in ``ddtrace-run`` or when debug mode is enabled",
    )

    report_hostname = En.v(bool, "trace.report_hostname", default=False)

    health_metrics_enabled = En.v(bool, "trace.health_metrics.enabled", default=False)

    trace_enabled = En.v(
        bool,
        "trace.enabled",
        default=True,
        help_type="Boolean",
        help="Enable sending of spans to the Agent. Note that instrumentation will still be installed "
        "and spans will be generated. Added in ``v0.41.0`` (formerly named ``DATADOG_TRACE_ENABLED``).",
    )

    _trace_agent_url = En.v(
        Optional[str],
        "trace.agent.url",
        default=None,
        help_type="URL",
        help_default="``unix:///var/run/datadog/apm.socket`` if available otherwise ``http://localhost:8126``",
        help="The URL to use to connect the Datadog agent for traces. The url can start with ``http://`` to connect "
        "using HTTP or with ``unix://`` to use a Unix Domain Socket. "
        "Example for http url: ``DD_TRACE_AGENT_URL=http://localhost:8126`` "
        "Example for UDS: ``DD_TRACE_AGENT_URL=unix:///var/run/datadog/apm.socket``",
    )
    trace_agent_url = En.d(
        str,
        lambda c: c._trace_agent_url or "unix:///var/run/datadog/apm.socket"
        if os.path.exists("/var/run/datadog/apm.socket")
        else "http://%s:%s" % (c.agent.hostname, c.agent.port),
    )

    trace_agent_timeout = En.v(
        float,
        "trace.agent.timeout_seconds",
        default=2.0,
        help_type="Float",
        help="The timeout in float to use to connect to the Datadog agent.",
    )

    trace_writer_buffer_size = En.v(
        int,
        "trace.writer.buffer_size_bytes",
        default=8 << 20,  # 8MB
        help_type="Int",
        help="The max size in bytes of traces to buffer between flushes to the agent.",
    )

    trace_writer_max_payload_size = En.v(
        int,
        "trace.writer.max_payload_size_bytes",
        default=8 << 20,  # 8MB
        help_type="Int",
        help="The max size in bytes of each payload item sent to the trace agent. If the max payload size is greater "
        "than buffer size, then max size of each payload item will be the buffer size.",
    )

    trace_writer_interval = En.v(
        float,
        "trace.writer.interval_seconds",
        default=1.0,
        help_type="Float",
        help="The time between each flush of traces to the trace agent.",
    )

    trace_startup_logs = En.v(
        bool,
        "trace.startup_logs",
        default=False,
        help_type="Boolean",
        help="Enable or disable start up diagnostic logging.",
    )

    trace_sample_rate = En.v(
        float,
        "trace.sample_rate",
        default=1.0,
        help_type="Float",
        help="A float, f, 0.0 <= f <= 1.0. f*100% of traces will be sampled.",
    )

    trace_sampling_rules = En.v(
        list,
        "trace.sampling_rules",
        default=[],
        parser=_parse_rules,
        help_type="JSON array",
        help_default="",
        help=dedent(
            """
        A JSON array of objects. Each object must have a “sample_rate”, and the “name” and “service” fields are
        "optional. The “sample_rate” value must be between 0.0 and 1.0 (inclusive).

        **Example:** ``DD_TRACE_SAMPLING_RULES=\'[{"sample_rate":0.5,"service":"my-service"}]\'``

        "**Note** that the JSON object must be included in single quotes (') to avoid problems with escaping of the
        double quote (\") character."""
        ),
    )

    trace_api_version = En.v(
        Optional[str],
        "trace.api_version",
        default=None,  # TODO: This is not correct
        validator=v.choice(["v0.%d" % v for v in range(3, 6)]),
        help_type="String",
        help_default="``v0.4`` if priority sampling is enabled, else ``v0.3``",
        help="The trace API version to use when sending traces to the Datadog agent. Currently, the supported "
        "versions are: ``v0.3``, ``v0.4`` and ``v0.5``.",
    )

    debug_enabled = En.v(
        bool,
        "trace.debug",
        default=False,
        help_type="Boolean",
        help="Enables debug logging in the tracer. Setting this flag will cause the library to create "
        "a root logging handler if one does not already exist. "
        "Added in ``v0.41.0`` (formerly named ``DATADOG_TRACE_DEBUG``). Can be used with `DD_TRACE_LOG_FILE` "
        "to route logs to a file.",
    )

    log_file_level = En.v(
        str,
        "trace.log_file.level",
        default="DEBUG",
        parser=lambda v: v.upper(),
        help_type="String",
        help="Configures the ``RotatingFileHandler`` used by the `ddtrace` logger to write logs to a file "
        "based on the level specified. Defaults to `DEBUG`, but will accept the values found in the standard "
        "**logging** library, such as WARNING, ERROR, and INFO, if further customization is needed. "
        "Files are not written to unless ``DD_TRACE_LOG_FILE`` has been defined.",
    )

    log_file = En.v(
        Optional[str],
        "trace.log_file",
        default=None,
        parser=os.path.abspath,
        help_type="String",
        help="Directs `ddtrace` logs to a specific file. Note: The default backup count is 1. For larger logs, use "
        "with ``DD_TRACE_LOG_FILE_SIZE_BYTES``. To fine tune the logging level, use with ``DD_TRACE_LOG_FILE_LEVEL``",
    )

    log_file_size = En.v(
        int,
        "trace.log_file.size_bytes",
        default=15 << 20,
        help_type="Int",
        help="Max size for a file when used with ``DD_TRACE_LOG_FILE``. When a log has exceeded this size, there will "
        "be one backup log file created. In total, the files will store ``2 * DD_TRACE_LOG_FILE_SIZE_BYTES`` worth "
        "of logs",
    )

    # Dummy variable. For documentation purposes only.
    _trace_integration_enabled = En.v(
        bool,
        "trace.<integration>.enabled",
        default=True,
        help_type="Boolean",
        help_default="(integration-dependent)",  # TODO: Verify this is correct
        help="Enables <INTEGRATION> to be patched. For example, ``DD_TRACE_DJANGO_ENABLED=false`` will disable the "
        "Django integration from being installed. Added in ``v0.41.0``.",
    )

    patch_modules = En.v(
        dict,
        "patch_modules",
        default={},
        parser=parse_tags_str,
        help_type="Mapping",
        help_default="",
        help="Override the modules patched for this execution of the program. Must be a list in the "
        "``module1:boolean,module2:boolean`` format. For example, ``boto:true,redis:false``. Added in ``v0.55.0`` "
        "(formerly named ``DATADOG_PATCH_MODULES``).",
    )

    telemetry_enabled = En.v(
        bool,
        "instrumentation_telemetry.enabled",
        default=False,
        help_type="Boolean",
        help="Enables sending telemetry events to the agent.",
    )

    _propagation_style_extract = En.v(
        set,
        "trace.propagation_style.extract",
        parser=_parse_propagation_styles,
        default={PROPAGATION_STYLE_DATADOG},
        help_type="String",
        help=dedent(
            """
        Comma separated list of propagation styles used for extracting trace context from inbound request headers.

        The supported values are ``datadog``, ``b3``, and ``b3 single header``.

        When checking inbound request headers we will take the first valid trace context in the order ``datadog``,
        ``b3``, then ``b3 single header``.

        Example: ``DD_TRACE_PROPAGATION_STYLE_EXTRACT="datadog,b3"`` to check for both ``x-datadog-*`` and ``x-b3-*``
        headers when parsing incoming request headers for a trace context."""
        ),
    )

    _propagation_style_inject = En.v(
        set,
        "trace.propagation_style.inject",
        parser=_parse_propagation_styles,
        default={PROPAGATION_STYLE_DATADOG},
        help_type="String",
        help=dedent(
            """
        Comma separated list of propagation styles used for injecting trace context into outbound request headers.

        The supported values are ``datadog``, ``b3``, and ``b3 single header``.

        All provided styles are injected into the headers of outbound requests.

        Example: ``DD_TRACE_PROPAGATION_STYLE_INJECT="datadog,b3"`` to inject both ``x-datadog-*`` and ``x-b3-*``
        headers into outbound requests."""
        ),
    )

    _x_datadog_tags_max_length = En.v(
        int,
        "trace.x_datadog_tags.max_length",
        validator=v.range(0, 512),
        default=512,
        help_type="Int",
        help="The maximum length of ``x-datadog-tags`` header allowed in the Datadog propagation style. Must be a "
        "value between 0 to 512. If 0, propagation of ``x-datadog-tags`` is disabled.",
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
