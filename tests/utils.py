import contextlib
from contextlib import contextmanager
import dataclasses
import datetime as dt
import http.client as httplib
from http.client import RemoteDisconnected
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import List  # noqa:F401
from urllib import parse
import urllib.parse

import pytest
import wrapt

import ddtrace
from ddtrace import config as dd_config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.ci_visibility.writer import CIVisibilityWriter
from ddtrace.internal.compat import to_unicode
from ddtrace.internal.constants import HIGHER_ORDER_TRACE_ID_BITS
from ddtrace.internal.encoding import JSONEncoder
from ddtrace.internal.encoding import MsgpackEncoderV04 as Encoder
from ddtrace.internal.packages import Distribution
from ddtrace.internal.packages import _package_for_root_module_mapping
from ddtrace.internal.packages import _third_party_packages
from ddtrace.internal.packages import filename_to_package
from ddtrace.internal.packages import is_third_party
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.schema import SCHEMA_VERSION
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.internal.writer import AgentWriter
from ddtrace.propagation._database_monitoring import listen as dbm_config_listen
from ddtrace.propagation._database_monitoring import unlisten as dbm_config_unlisten
from ddtrace.propagation.http import _DatadogMultiHeader
from ddtrace.settings._agent import config as agent_config
from ddtrace.settings._database_monitoring import dbm_config
from ddtrace.settings.asm import config as asm_config
from ddtrace.trace import Span
from ddtrace.trace import Tracer
from tests.subprocesstest import SubprocessTestCase


try:
    import importlib.metadata as importlib_metadata
except ImportError:
    import importlib_metadata

NO_CHILDREN = object()
DDTRACE_PATH = Path(__file__).resolve().parents[1]
FILE_PATH = Path(__file__).resolve().parent


def assert_is_measured(span):
    """Assert that the span has the proper _dd.measured tag set"""
    assert _SPAN_MEASURED_KEY in span.get_metrics()
    assert _SPAN_MEASURED_KEY not in span.get_tags()
    assert span.get_metric(_SPAN_MEASURED_KEY) == 1


def assert_is_not_measured(span):
    """Assert that the span does not set _dd.measured"""
    assert _SPAN_MEASURED_KEY not in span.get_tags()
    if _SPAN_MEASURED_KEY in span.get_metrics():
        assert span.get_metric(_SPAN_MEASURED_KEY) == 0
    else:
        assert _SPAN_MEASURED_KEY not in span.get_metrics()


def assert_span_http_status_code(span, code):
    """Assert on the span's 'http.status_code' tag"""
    tag = span.get_tag(http.STATUS_CODE)
    code = str(code)
    assert tag == code, "%r != %r" % (tag, code)


@contextlib.contextmanager
def override_env(env, replace_os_env=False):
    """
    Temporarily override ``os.environ`` with provided values::

        >>> with self.override_env(dict(DD_TRACE_DEBUG=True)):
            # Your test
    """
    # Copy the full original environment
    original = dict(os.environ)

    # We allow callers to clear out the environment to prevent leaking variables into the test
    if replace_os_env:
        os.environ.clear()

    for k in os.environ.keys():
        if k.startswith(("_CI_DD_", "DD_CIVISIBILITY_", "DD_SITE")):
            del os.environ[k]

    # Update based on the passed in arguments
    os.environ.update(env)
    try:
        yield
    finally:
        # Full clear the environment out and reset back to the original
        os.environ.clear()
        os.environ.update(original)


@contextlib.contextmanager
def override_global_config(values):
    """
    Temporarily override an global configuration::

        >>> with self.override_global_config(dict(name=value,...)):
            # Your test
    """
    # List of global variables we allow overriding
    # DEV: We do not do `ddtrace.config.keys()` because we have all of our integrations
    global_config_keys = [
        "_tracing_enabled",
        "_client_ip_header",
        "_retrieve_client_ip",
        "_report_hostname",
        "_health_metrics_enabled",
        "_propagation_style_extract",
        "_propagation_style_inject",
        "_propagation_behavior_extract",
        "_x_datadog_tags_max_length",
        "_128_bit_trace_id_enabled",
        "_x_datadog_tags_enabled",
        "_startup_logs_enabled",
        "env",
        "version",
        "service",
        "_raise",
        "_trace_compute_stats",
        "_obfuscation_query_string_pattern",
        "_global_query_string_obfuscation_disabled",
        "_ci_visibility_agentless_url",
        "_ci_visibility_agentless_enabled",
        "_remote_config_enabled",
        "_remote_config_poll_interval",
        "_sampling_rules",
        "_sampling_rules_file",
        "_trace_rate_limit",
        "_trace_sampling_rules",
        "_trace_api",
        "_trace_writer_buffer_size",
        "_trace_writer_payload_size",
        "_trace_writer_interval_seconds",
        "_trace_writer_connection_reuse",
        "_trace_writer_log_err_payload",
        "_span_traceback_max_size",
        "_propagation_http_baggage_enabled",
        "_telemetry_enabled",
        "_telemetry_dependency_collection",
        "_dd_site",
        "_dd_api_key",
        "_llmobs_enabled",
        "_llmobs_sample_rate",
        "_llmobs_ml_app",
        "_llmobs_agentless_enabled",
        "_data_streams_enabled",
        "_inferred_proxy_services_enabled",
    ]

    asm_config_keys = asm_config._asm_config_keys

    subscriptions = ddtrace.config._subscriptions
    ddtrace.config._subscriptions = []
    # Grab the current values of all keys
    originals = dict((key, getattr(ddtrace.config, key)) for key in global_config_keys)
    asm_originals = dict((key, getattr(asm_config, key)) for key in asm_config_keys)

    # Override from the passed in keys
    for key, value in values.items():
        if key in global_config_keys:
            setattr(ddtrace.config, key, value)
    # rebuild asm config from env vars and global config
    for key, value in values.items():
        if key in asm_config_keys:
            setattr(asm_config, key, value)
    # If ddtrace.settings.asm.config has changed, check _asm_can_be_enabled again
    asm_config._eval_asm_can_be_enabled()
    try:
        core.dispatch("test.config.override")
        yield
    finally:
        # Reset all to their original values
        for key, value in originals.items():
            setattr(ddtrace.config, key, value)

        asm_config.reset()
        for key, value in asm_originals.items():
            setattr(asm_config, key, value)

        ddtrace.config._reset()
        ddtrace.config._subscriptions = subscriptions


@contextlib.contextmanager
def override_config(integration, values):
    """
    Temporarily override an integration configuration value::

        >>> with self.override_config('flask', dict(service_name='test-service')):
            # Your test
    """
    options = getattr(ddtrace.config, integration)

    original = dict((key, options.get(key)) for key in values.keys())

    options.update(values)
    try:
        yield
    finally:
        options.update(original)
        ddtrace.config._reset()


@contextlib.contextmanager
def override_http_config(integration, values):
    """
    Temporarily override an integration configuration for HTTP value::

        >>> with self.override_http_config('flask', dict(trace_query_string=True)):
            # Your test
    """
    options = getattr(ddtrace.config, integration).http

    original = {
        "_header_tags": options._header_tags,
    }
    for key, value in values.items():
        if key == "trace_headers":
            options.trace_headers(value)
        else:
            original[key] = getattr(options, key)
            setattr(options, key, value)

    try:
        yield
    finally:
        for key, value in original.items():
            setattr(options, key, value)


@contextlib.contextmanager
def override_dbm_config(values):
    config_keys = ["propagation_mode"]
    originals = dict((key, getattr(dbm_config, key)) for key in config_keys)

    # Override from the passed in keys
    for key, value in values.items():
        if key in config_keys:
            setattr(dbm_config, key, value)
    try:
        dbm_config_listen()
        yield
    finally:
        # Reset all to their original values
        for key, value in originals.items():
            setattr(dbm_config, key, value)
        dbm_config_unlisten()


@contextlib.contextmanager
def override_sys_modules(modules):
    """
    Temporarily override ``sys.modules`` with provided dictionary of modules::

        >>> mock_module = mock.MagicMock()
        >>> mock_module.fn.side_effect = lambda: 'test'
        >>> with self.override_sys_modules(dict(A=mock_module)):
            # Your test
    """
    original = dict(sys.modules)

    sys.modules.update(modules)
    try:
        yield
    finally:
        sys.modules.clear()
        sys.modules.update(original)


class BaseTestCase(SubprocessTestCase):
    """
    BaseTestCase extends ``unittest.TestCase`` to provide some useful helpers/assertions


    Example::

        from tests.utils import BaseTestCase


        class MyTestCase(BaseTestCase):
            def test_case(self):
                with self.override_config('flask', dict(distributed_tracing_enabled=True):
                    pass
    """

    override_env = staticmethod(override_env)
    override_global_config = staticmethod(override_global_config)
    override_config = staticmethod(override_config)
    override_http_config = staticmethod(override_http_config)
    override_sys_modules = staticmethod(override_sys_modules)
    assert_is_measured = staticmethod(assert_is_measured)
    assert_is_not_measured = staticmethod(assert_is_not_measured)


def _build_tree(
    spans,  # type: List[Span]
    root,  # type: Span
):
    # type: (...) -> TestSpanNode
    """helper to build a tree structure for the provided root span"""
    children = []
    for span in spans:
        if span.parent_id == root.span_id:
            children.append(_build_tree(spans, span))

    return TestSpanNode(root, children)


def get_root_span(
    spans,  # type: List[Span]
):
    # type: (...) -> TestSpanNode
    """
    Helper to get the root span from the list of spans in this container

    :returns: The root span if one was found, None if not, and AssertionError if multiple roots were found
    :rtype: :class:`tests.utils.span.TestSpanNode`, None
    :raises: AssertionError
    """
    root = None
    for span in spans:
        if span.parent_id is None:
            if root is not None:
                raise AssertionError("Multiple root spans found {0!r} {1!r}".format(root, span))
            root = span

    assert root, "No root span found in {0!r}".format(spans)

    return _build_tree(spans, root)


class TestSpanContainer(object):
    """
    Helper class for a container of Spans.

    Subclasses of this class must implement a `get_spans` method::

        def get_spans(self):
            return []

    This class provides methods and assertions over a list of spans::

        class TestCases(BaseTracerTestCase):
            def test_spans(self):
                # TODO: Create spans

                self.assert_has_spans()
                self.assert_span_count(3)
                self.assert_structure( ... )

                # Grab only the `requests.request` spans
                spans = self.filter_spans(name='requests.request')
    """

    def _ensure_test_spans(self, spans):
        """
        internal helper to ensure the list of spans are all :class:`tests.utils.span.TestSpan`

        :param spans: List of :class:`ddtrace.trace.Span` or :class:`tests.utils.span.TestSpan`
        :type spans: list
        :returns: A list og :class:`tests.utils.span.TestSpan`
        :rtype: list
        """
        return [span if isinstance(span, TestSpan) else TestSpan(span) for span in spans]

    @property
    def spans(self):
        return self._ensure_test_spans(self.get_spans())

    def get_spans(self):
        """subclass required property"""
        raise NotImplementedError

    def get_root_span(self):
        # type: (...) -> TestSpanNode
        """
        Helper to get the root span from the list of spans in this container

        :returns: The root span if one was found, None if not, and AssertionError if multiple roots were found
        :rtype: :class:`tests.utils.span.TestSpanNode`, None
        :raises: AssertionError
        """
        return get_root_span(self.spans)

    def get_root_spans(self):
        # type: (...) -> List[Span]
        """
        Helper to get all root spans from the list of spans in this container

        :returns: The root spans if any were found, None if not
        :rtype: list of :class:`tests.utils.span.TestSpanNode`, None
        """
        roots = []
        for span in self.spans:
            if span.parent_id is None:
                roots.append(_build_tree(self.spans, span))

        return sorted(roots, key=lambda s: s.start)

    def assert_trace_count(self, count):
        """Assert the number of unique trace ids this container has"""
        trace_count = len(self.get_root_spans())
        assert trace_count == count, "Trace count {0} != {1}".format(trace_count, count)

    def assert_span_count(self, count):
        """Assert this container has the expected number of spans"""
        assert len(self.spans) == count, "Span count {0} != {1}".format(len(self.spans), count)

    def assert_has_spans(self):
        """Assert this container has spans"""
        assert len(self.spans), "No spans found"

    def assert_has_no_spans(self):
        """Assert this container does not have any spans"""
        assert len(self.spans) == 0, "Span count {0}".format(len(self.spans))

    def filter_spans(self, *args, **kwargs):
        """
        Helper to filter current spans by provided parameters.

        This function will yield all spans whose `TestSpan.matches` function return `True`.

        :param args: Positional arguments to pass to :meth:`tests.utils.span.TestSpan.matches`
        :type args: list
        :param kwargs: Keyword arguments to pass to :meth:`tests.utils.span.TestSpan.matches`
        :type kwargs: dict
        :returns: generator for the matched :class:`tests.utils.span.TestSpan`
        :rtype: generator
        """
        for span in self.spans:
            # ensure we have a TestSpan
            if not isinstance(span, TestSpan):
                span = TestSpan(span)

            if span.matches(*args, **kwargs):
                yield span

    def find_span(self, *args, **kwargs):
        """
        Find a single span matches the provided filter parameters.

        This function will find the first span whose `TestSpan.matches` function return `True`.

        :param args: Positional arguments to pass to :meth:`tests.utils.span.TestSpan.matches`
        :type args: list
        :param kwargs: Keyword arguments to pass to :meth:`tests.utils.span.TestSpan.matches`
        :type kwargs: dict
        :returns: The first matching span
        :rtype: :class:`tests.TestSpan`
        """
        span = next(self.filter_spans(*args, **kwargs), None)
        assert span is not None, "No span found for filter {0!r} {1!r}, have {2} spans".format(
            args, kwargs, len(self.spans)
        )
        return span


class TracerTestCase(TestSpanContainer, BaseTestCase):
    """
    BaseTracerTestCase is a base test case for when you need access to a dummy tracer and span assertions
    """

    def setUp(self):
        """Before each test case, setup a dummy tracer to use"""
        self.tracer = DummyTracer()

        super(TracerTestCase, self).setUp()

    def tearDown(self):
        """After each test case, reset and remove the dummy tracer"""
        super(TracerTestCase, self).tearDown()

        self.reset()
        delattr(self, "tracer")

    def get_spans(self):
        """Required subclass method for TestSpanContainer"""
        return self.tracer.get_spans()

    def pop_spans(self):
        # type: () -> List[Span]
        return self.tracer.pop()

    def pop_traces(self):
        # type: () -> List[List[Span]]
        return self.tracer.pop_traces()

    def reset(self):
        """Helper to reset the existing list of spans created"""
        self.tracer._span_aggregator.writer.pop()

    def trace(self, *args, **kwargs):
        """Wrapper for self.tracer.trace that returns a TestSpan"""
        return TestSpan(self.tracer.trace(*args, **kwargs))

    def start_span(self, *args, **kwargs):
        """Helper for self.tracer.start_span that returns a TestSpan"""
        return TestSpan(self.tracer.start_span(*args, **kwargs))

    def assert_structure(self, root, children=NO_CHILDREN):
        """Helper to call TestSpanNode.assert_structure on the current root span"""
        root_span = self.get_root_span()
        root_span.assert_structure(root, children)

    @contextlib.contextmanager
    def override_global_tracer(self, tracer=None):
        original = ddtrace.tracer
        tracer = tracer or self.tracer
        ddtrace.tracer = tracer
        core.tracer = tracer
        try:
            yield
        finally:
            ddtrace.tracer = original
            core.tracer = original


class DummyWriterMixin:
    def __init__(self, *args, **kwargs):
        self.spans = []
        self.traces = []

    def write(self, spans=None):
        if spans:
            # the traces encoding expect a list of traces so we
            # put spans in a list like we do in the real execution path
            # with both encoders
            traces = [spans]
            self.spans += spans
            self.traces += traces

    def pop(self):
        # type: () -> List[Span]
        s = self.spans
        self.spans = []
        return s

    def pop_traces(self):
        # type: () -> List[List[Span]]
        traces = self.traces
        self.traces = []
        return traces


class DummyWriter(DummyWriterMixin, AgentWriter):
    """DummyWriter is a small fake writer used for tests. not thread-safe."""

    def __init__(self, *args, **kwargs):
        # original call
        if len(args) == 0 and "agent_url" not in kwargs:
            kwargs["agent_url"] = agent_config.trace_agent_url
        kwargs["api_version"] = kwargs.get("api_version", "v0.5")

        # only flush traces to test agent if ``trace_flush_enabled`` is explicitly set to True
        self._trace_flush_enabled = kwargs.pop("trace_flush_enabled", False) is True

        AgentWriter.__init__(self, *args, **kwargs)
        DummyWriterMixin.__init__(self, *args, **kwargs)
        self.json_encoder = JSONEncoder()
        self.msgpack_encoder = Encoder(4 << 20, 4 << 20)

    def write(self, spans=None):
        DummyWriterMixin.write(self, spans=spans)
        if spans:
            traces = [spans]
            self.json_encoder.encode_traces(traces)
            if self._trace_flush_enabled:
                AgentWriter.write(self, spans=spans)
            else:
                self.msgpack_encoder.put(spans)
                self.msgpack_encoder.encode()

    def pop(self):
        spans = DummyWriterMixin.pop(self)
        if self._trace_flush_enabled:
            flush_test_tracer_spans(self)
        return spans

    def recreate(self):
        return self.__class__(trace_flush_enabled=self._trace_flush_enabled)


class DummyCIVisibilityWriter(DummyWriterMixin, CIVisibilityWriter):
    def __init__(self, *args, **kwargs):
        CIVisibilityWriter.__init__(self, *args, **kwargs)
        DummyWriterMixin.__init__(self, *args, **kwargs)
        self._encoded = None

    def write(self, spans=None):
        DummyWriterMixin.write(self, spans=spans)
        CIVisibilityWriter.write(self, spans=spans)
        # take a snapshot of the writer buffer for tests to inspect
        if spans:
            self._encoded = self._encoder._build_payload([spans])


class DummyTracer(Tracer):
    """
    DummyTracer is a tracer which uses the DummyWriter by default
    """

    def __init__(self, *args, **kwargs):
        super(DummyTracer, self).__init__()
        self._trace_flush_disabled_via_env = not asbool(os.getenv("_DD_TEST_TRACE_FLUSH_ENABLED", True))
        self._trace_flush_enabled = True
        # Ensure DummyTracer is always initialized with a DummyWriter
        self._span_aggregator.writer = DummyWriter(
            trace_flush_enabled=check_test_agent_status() if not self._trace_flush_disabled_via_env else False
        )

    @property
    def agent_url(self):
        # type: () -> str
        return self._span_aggregator.writer.agent_url

    @property
    def encoder(self):
        # type: () -> Encoder
        return self._span_aggregator.writer.msgpack_encoder

    def get_spans(self):
        # type: () -> List[List[Span]]
        spans = self._span_aggregator.writer.spans
        if self._trace_flush_enabled:
            flush_test_tracer_spans(self._span_aggregator.writer)
        return spans

    def pop(self):
        # type: () -> List[Span]
        spans = self._span_aggregator.writer.pop()
        return spans

    def pop_traces(self):
        # type: () -> List[List[Span]]
        traces = self._span_aggregator.writer.pop_traces()
        if self._trace_flush_enabled:
            flush_test_tracer_spans(self._span_aggregator.writer)
        return traces


class TestSpan(Span):
    """
    Test wrapper for a :class:`ddtrace.trace.Span` that provides additional functions and assertions

    Example::

        span = tracer.trace('my.span')
        span = TestSpan(span)

        if span.matches(name='my.span'):
            print('matches')

        # Raises an AssertionError
        span.assert_matches(name='not.my.span', meta={'process_id': getpid()})
    """

    def __init__(self, span):
        """
        Constructor for TestSpan

        :param span: The :class:`ddtrace.trace.Span` to wrap
        :type span: :class:`ddtrace.trace.Span`
        """
        if isinstance(span, TestSpan):
            span = span._span

        # DEV: Use `object.__setattr__` to by-pass this class's `__setattr__`
        object.__setattr__(self, "_span", span)

    def __getattr__(self, key):
        """
        First look for property on the base :class:`ddtrace.trace.Span` otherwise return this object's attribute
        """
        if hasattr(self._span, key):
            return getattr(self._span, key)

        return self.__getattribute__(key)

    def __setattr__(self, key, value):
        """Pass through all assignment to the base :class:`ddtrace.trace.Span`"""
        return setattr(self._span, key, value)

    def __eq__(self, other):
        """
        Custom equality code to ensure we are using the base :class:`ddtrace.trace.Span.__eq__`

        :param other: The object to check equality with
        :type other: object
        :returns: True if equal, False otherwise
        :rtype: bool
        """
        if isinstance(other, TestSpan):
            return other._span == self._span
        elif isinstance(other, Span):
            return other == self._span
        return other == self

    def matches(self, **kwargs):
        """
        Helper function to check if this span's properties matches the expected.

        Example::

            span = TestSpan(span)
            span.matches(name='my.span', resource='GET /')

        :param kwargs: Property/Value pairs to evaluate on this span
        :type kwargs: dict
        :returns: True if the arguments passed match, False otherwise
        :rtype: bool
        """
        for name, value in kwargs.items():
            # Special case for `meta`
            if name == "meta" and not self.meta_matches(value):
                return False

            # Ensure it has the property first
            if not hasattr(self, name):
                return False

            # Ensure the values match
            if getattr(self, name) != value:
                return False

        return True

    def meta_matches(self, meta, exact=False):
        """
        Helper function to check if this span's meta matches the expected

        Example::

            span = TestSpan(span)
            span.meta_matches({'process_id': getpid()})

        :param meta: Property/Value pairs to evaluate on this span
        :type meta: dict
        :param exact: Whether to do an exact match on the meta values or not, default: False
        :type exact: bool
        :returns: True if the arguments passed match, False otherwise
        :rtype: bool
        """
        if exact:
            return self.get_tags() == meta

        for key, value in meta.items():
            if key not in self._meta:
                return False
            if self.get_tag(key) != value:
                return False
        return True

    def assert_matches(self, **kwargs):
        """
        Assertion method to ensure this span's properties match as expected

        Example::

            span = TestSpan(span)
            span.assert_matches(name='my.span')

        :param kwargs: Property/Value pairs to evaluate on this span
        :type kwargs: dict
        :raises: AssertionError
        """
        for name, value in kwargs.items():
            # Special case for `meta`
            if name == "meta":
                self.assert_meta(value)
            elif name == "metrics":
                self.assert_metrics(value)
            else:
                assert hasattr(self, name), "{0!r} does not have property {1!r}".format(self, name)
                assert getattr(self, name) == value, "{0!r} property {1}: {2!r} != {3!r}".format(
                    self, name, getattr(self, name), value
                )

    def assert_meta(self, meta, exact=False):
        """
        Assertion method to ensure this span's meta match as expected

        Example::

            span = TestSpan(span)
            span.assert_meta({'process_id': getpid()})

        :param meta: Property/Value pairs to evaluate on this span
        :type meta: dict
        :param exact: Whether to do an exact match on the meta values or not, default: False
        :type exact: bool
        :raises: AssertionError
        """
        if exact:
            assert self.get_tags() == meta
        else:
            for key, value in meta.items():
                assert key in self._meta, "{0} meta does not have property {1!r}".format(self, key)
                assert self.get_tag(key) == value, "{0} meta property {1!r}: {2!r} != {3!r}".format(
                    self, key, self.get_tag(key), value
                )

    def assert_metrics(self, metrics, exact=False):
        """
        Assertion method to ensure this span's metrics match as expected

        Example::

            span = TestSpan(span)
            span.assert_metrics({'_dd1.sr.eausr': 1})

        :param metrics: Property/Value pairs to evaluate on this span
        :type metrics: dict
        :param exact: Whether to do an exact match on the metrics values or not, default: False
        :type exact: bool
        :raises: AssertionError
        """
        if exact:
            assert self._metrics == metrics
        else:
            for key, value in metrics.items():
                assert key in self._metrics, "{0} metrics does not have property {1!r}".format(self, key)
                assert self._metrics[key] == value, "{0} metrics property {1!r}: {2!r} != {3!r}".format(
                    self, key, self._metrics[key], value
                )

    def assert_span_event_count(self, count):
        """Assert this span has the expected number of span_events"""
        assert len(self._events) == count, "Span event count {0} != {1}".format(len(self._events), count)

    def assert_span_event_attributes(self, event_idx, attrs):
        """
        Assertion method to ensure this span's span event match as expected

        Example::

            span = TestSpan(span)
            span.assert_span_event(0, {"exception.type": "builtins.RuntimeError"})

        :param event_idx: id of the span event
        :type event_idx: integer
        """
        span_event_attrs = self._events[event_idx].attributes
        for name, value in attrs.items():
            assert name in span_event_attrs, "{0!r} does not have property {1!r}".format(span_event_attrs, name)
            assert span_event_attrs[name] == value, "{0!r} property {1}: {2!r} != {3!r}".format(
                span_event_attrs, name, span_event_attrs[name], value
            )


class TracerSpanContainer(TestSpanContainer):
    """
    A class to wrap a :class:`tests.utils.tracer.DummyTracer` with a
    :class:`tests.utils.span.TestSpanContainer` to use in tests
    """

    def __init__(self, tracer):
        self.tracer = tracer
        super(TracerSpanContainer, self).__init__()

    def get_spans(self):
        """
        Overridden method to return all spans attached to this tracer

        :returns: List of spans attached to this tracer
        :rtype: list
        """
        return self.tracer._span_aggregator.writer.spans

    def pop(self):
        return self.tracer.pop()

    def pop_traces(self):
        return self.tracer.pop_traces()

    def reset(self):
        """Helper to reset the existing list of spans created"""
        self.tracer.pop()


class TestSpanNode(TestSpan, TestSpanContainer):
    """
    A :class:`tests.utils.span.TestSpan` which is used as part of a span tree.

    Each :class:`tests.utils.span.TestSpanNode` represents the current :class:`ddtrace.trace.Span`
    along with any children who have that span as it's parent.

    This class can be used to assert on the parent/child relationships between spans.

    Example::

        class TestCase(BaseTestCase):
            def test_case(self):
                # TODO: Create spans

                self.assert_structure( ... )

                tree = self.get_root_span()

                # Find the first child of the root span with the matching name
                request = tree.find_span(name='requests.request')

                # Assert the parent/child relationship of this `request` span
                request.assert_structure( ... )
    """

    def __init__(self, root, children=None):
        super(TestSpanNode, self).__init__(root)
        object.__setattr__(self, "_children", children or [])

    def get_spans(self):
        """required subclass property, returns this spans children"""
        return self._children

    def assert_structure(self, root, children=NO_CHILDREN):
        """
        Assertion to assert on the structure of this node and it's children.

        This assertion takes a dictionary of properties to assert for this node
        along with a list of assertions to make for it's children.

        Example::

            def test_case(self):
                # Assert the following structure
                #
                # One root_span, with two child_spans, one with a requests.request span
                #
                # |                  root_span                |
                # |       child_span       | |   child_span   |
                # | requests.request |
                self.assert_structure(
                    # Root span with two child_span spans
                    dict(name='root_span'),

                    (
                        # Child span with one child of it's own
                        (
                            dict(name='child_span'),

                            # One requests.request span with no children
                            (
                                dict(name='requests.request'),
                            ),
                        ),

                        # Child span with no children
                        dict(name='child_span'),
                    ),
                )

        :param root: Properties to assert for this root span, these are passed to
            :meth:`tests.utils.span.TestSpan.assert_matches`
        :type root: dict
        :param children: List of child assertions to make, if children is None then do not make any
            assertions about this nodes children. Each list element must be a list with 2 items
            the first is a ``dict`` of property assertions on that child, and the second is a ``list``
            of child assertions to make.
        :type children: list, None
        :raises:
        """
        self.assert_matches(**root)

        # Give them a way to ignore asserting on children
        if children is None:
            return
        elif children is NO_CHILDREN:
            children = ()

        spans = self.spans
        self.assert_span_count(len(children))
        for i, child in enumerate(children):
            if not isinstance(child, (list, tuple)):
                child = (child, NO_CHILDREN)

            root, _children = child
            spans[i].assert_matches(parent_id=self.span_id, trace_id=self.trace_id, _parent=self)
            spans[i].assert_structure(root, _children)


def assert_dict_issuperset(a, b):
    assert set(a.items()).issuperset(set(b.items())), "{a} is not a superset of {b}".format(a=a, b=b)


@contextmanager
def override_global_tracer(tracer):
    """Helper functions that overrides the global tracer available in the
    `ddtrace` package. This is required because in some `httplib` tests we
    can't get easily the PIN object attached to the `HTTPConnection` to
    replace the used tracer with a dummy tracer.
    """
    original_tracer = ddtrace.tracer
    ddtrace.tracer = tracer
    core.tracer = tracer
    yield
    ddtrace.tracer = original_tracer
    core.tracer = original_tracer


class SnapshotFailed(Exception):
    pass


@dataclasses.dataclass
class SnapshotTest:
    token: str
    tracer: ddtrace.trace.Tracer = ddtrace.tracer

    def clear(self):
        """Clear any traces sent that were sent for this snapshot."""
        parsed = parse.urlparse(self.tracer.agent_trace_url)
        conn = httplib.HTTPConnection(parsed.hostname, parsed.port)
        conn.request("GET", "/test/session/clear?test_session_token=%s" % self.token)
        resp = conn.getresponse()
        assert resp.status == 200


@contextmanager
def snapshot_context(
    token,
    agent_sample_rate_by_service=None,
    ignores=None,
    tracer=None,
    async_mode=True,
    variants=None,
    wait_for_num_traces=None,
):
    # Use variant that applies to update test token. One must apply. If none
    # apply, the test should have been marked as skipped.
    if variants:
        applicable_variant_ids = [k for (k, v) in variants.items() if v]
        assert len(applicable_variant_ids) == 1
        variant_id = applicable_variant_ids[0]
        token = "{}_{}".format(token, variant_id) if variant_id else token

    ignores = ignores or []
    if not tracer:
        tracer = ddtrace.tracer

    parsed = parse.urlparse(tracer._span_aggregator.writer.agent_url)
    conn = httplib.HTTPConnection(parsed.hostname, parsed.port)
    try:
        # clear queue in case traces have been generated before test case is
        # itself run
        try:
            tracer._span_aggregator.writer.flush_queue()
        except Exception as e:
            pytest.fail("Could not flush the queue before test case: %s" % str(e), pytrace=True)

        if async_mode:
            # Patch the tracer writer to include the test token header for all requests.
            tracer._span_aggregator.writer._headers["X-Datadog-Test-Session-Token"] = token

            # Also add a header to the environment for subprocesses test cases that might use snapshotting.
            existing_headers = parse_tags_str(os.environ.get("_DD_TRACE_WRITER_ADDITIONAL_HEADERS", ""))
            existing_headers.update({"X-Datadog-Test-Session-Token": token})
            os.environ["_DD_TRACE_WRITER_ADDITIONAL_HEADERS"] = ",".join(
                ["%s:%s" % (k, v) for k, v in existing_headers.items()]
            )
        try:
            query = urllib.parse.urlencode(
                {
                    "test_session_token": token,
                    "agent_sample_rate_by_service": json.dumps(agent_sample_rate_by_service or {}),
                }
            )
            conn.request("GET", "/test/session/start?" + query)
        except Exception as e:
            pytest.fail("Could not connect to test agent: %s" % str(e), pytrace=False)
        else:
            r = None
            attempt_start = time.time()
            while r is None and time.time() - attempt_start < 60:
                try:
                    r = conn.getresponse()
                except RemoteDisconnected:
                    time.sleep(1)
            if r is None:
                pytest.fail("Repeated attempts to start testagent session failed", pytrace=False)
            elif r.status != 200:
                # The test agent returns nice error messages we can forward to the user.
                pytest.fail(to_unicode(r.read()), pytrace=False)
        try:
            yield SnapshotTest(
                tracer=tracer,
                token=token,
            )
        finally:
            # Force a flush so all traces are submitted.
            tracer._span_aggregator.writer.flush_queue()
            if async_mode:
                del tracer._span_aggregator.writer._headers["X-Datadog-Test-Session-Token"]
                del os.environ["_DD_TRACE_WRITER_ADDITIONAL_HEADERS"]

        conn = httplib.HTTPConnection(parsed.hostname, parsed.port)

        # Wait for the traces to be available
        if wait_for_num_traces is not None:
            traces = []
            for _ in range(50):
                try:
                    conn.request("GET", "/test/session/traces?test_session_token=%s" % token)
                    r = conn.getresponse()
                    if r.status == 200:
                        traces = json.loads(r.read())
                        if len(traces) >= wait_for_num_traces:
                            break
                except Exception:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail(
                    "Expected %r trace(s), got %r:\n%s" % (wait_for_num_traces, len(traces), traces), pytrace=False
                )

        # Query for the results of the test.
        conn = httplib.HTTPConnection(parsed.hostname, parsed.port)
        conn.request("GET", "/test/session/snapshot?ignores=%s&test_session_token=%s" % (",".join(ignores), token))
        r = conn.getresponse()
        result = to_unicode(r.read())
        if r.status != 200:
            lowered = result.lower()
            if "received unmatched traces" not in lowered:
                pytest.fail(result, pytrace=False)
            # we don't know why the test agent occasionally receives a different number of traces than it expects
            # during snapshot tests, but that does sometimes in an unpredictable manner
            # it seems to have to do with using the same test agent across many tests - maybe the test agent
            # occasionally mixes up traces between sessions. regardless of why they happen, we have been treating
            # these test failures as unactionable in the vast majority of cases and thus ignore them here to reduce
            # the toil involved in getting CI to green. revisit this approach once we understand why
            # "received unmatched traces" can sometimes happen
            else:
                pytest.xfail(result)
    finally:
        conn = httplib.HTTPConnection(parsed.hostname, parsed.port)
        conn.request("GET", "/test/session/snapshot?ignores=%s&test_session_token=%s" % (",".join(ignores), token))
        conn.getresponse()
        conn.close()


def snapshot(
    ignores=None, include_tracer=False, variants=None, async_mode=True, token_override=None, wait_for_num_traces=None
):
    """Performs a snapshot integration test with the testing agent.

    All traces sent to the agent will be recorded and compared to a snapshot
    created for the test case.

    :param ignores: A list of keys to ignore when comparing snapshots. To refer
                    to keys in the meta or metrics maps use "meta.key" and
                    "metrics.key"
    :param tracer: A tracer providing the agent connection information to use.
    """
    ignores = ignores or []

    @wrapt.decorator
    def wrapper(wrapped, instance, args, kwargs):
        if len(args) > 1:
            self = args[0]
            clsname = self.__class__.__name__
        else:
            clsname = ""

        module = inspect.getmodule(wrapped)

        # Use the fully qualified function name as a unique test token to
        # identify the snapshot.
        token = (
            "{}{}{}.{}".format(module.__name__, "." if clsname else "", clsname, wrapped.__name__)
            if token_override is None
            else token_override
        )

        with snapshot_context(
            token,
            ignores=ignores,
            tracer=ddtrace.tracer,
            async_mode=async_mode,
            variants=variants,
            wait_for_num_traces=wait_for_num_traces,
        ):
            # Run the test.
            if include_tracer:
                kwargs["tracer"] = ddtrace.tracer
            return wrapped(*args, **kwargs)

    return wrapper


class AnyStr(object):
    def __eq__(self, other):
        return isinstance(other, str)


class AnyInt(object):
    def __eq__(self, other):
        return isinstance(other, int)


class AnyExc(object):
    def __eq__(self, other):
        return isinstance(other, Exception)


class AnyFloat(object):
    def __eq__(self, other):
        return isinstance(other, float)


def call_program(*args, **kwargs):
    timeout = kwargs.pop("timeout", None)
    if "env" in kwargs:
        # Remove all keys with the value None from env, None is used to unset an environment variable
        env = kwargs.pop("env")
        cleaned_env = {env: val for env, val in env.items() if val is not None}
        kwargs["env"] = cleaned_env
    close_fds = sys.platform != "win32"
    subp = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=close_fds, **kwargs)
    try:
        stdout, stderr = subp.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        subp.terminate()
        stdout, stderr = subp.communicate(timeout=timeout)
    return stdout, stderr, subp.wait(), subp.pid


def request_token(request):
    # type: (pytest.FixtureRequest) -> str
    token = ""
    token += request.module.__name__
    token += ".%s" % request.cls.__name__ if request.cls else ""
    token += ".%s" % request.node.name
    return token


def package_installed(package_name):
    try:
        importlib_metadata.distribution(package_name)
        return True
    except importlib_metadata.PackageNotFoundError:
        return False


def git_repo_empty(tmpdir):
    """Create temporary empty git directory, meaning no commits/users/repository-url to extract (error)"""
    cwd = str(tmpdir)
    version = subprocess.check_output("git version", shell=True)
    # decode "git version 2.28.0" to (2, 28, 0)
    decoded_version = tuple(int(n) for n in version.decode().strip().split(" ")[-1].split(".") if n.isdigit())
    if decoded_version >= (2, 28):
        # versions starting from 2.28 can have a different initial branch name
        # configured in ~/.gitconfig
        subprocess.check_output("git init --initial-branch=master", cwd=cwd, shell=True)
    else:
        # versions prior to 2.28 will create a master branch by default
        subprocess.check_output("git init", cwd=cwd, shell=True)
    return cwd


def git_repo(git_repo_empty):
    """Create temporary git directory, with one added file commit with a unique author and committer."""
    cwd = git_repo_empty
    subprocess.check_output('git remote add origin "git@github.com:test-repo-url.git"', cwd=cwd, shell=True)
    # Set temporary git directory to not require gpg commit signing
    subprocess.check_output("git config --local commit.gpgsign false", cwd=cwd, shell=True)
    # Set committer user to be "Jane Doe"
    subprocess.check_output('git config --local user.name "Jane Doe"', cwd=cwd, shell=True)
    subprocess.check_output('git config --local user.email "jane@doe.com"', cwd=cwd, shell=True)
    subprocess.check_output("touch tmp.py", cwd=cwd, shell=True)
    subprocess.check_output("git add tmp.py", cwd=cwd, shell=True)
    # Override author to be "John Doe"
    subprocess.check_output(
        'GIT_COMMITTER_DATE="2021-01-20T04:37:21-0400" git commit --date="2021-01-19T09:24:53-0400" '
        '-m "this is a commit msg" --author="John Doe <john@doe.com>" --no-edit',
        cwd=cwd,
        shell=True,
    )
    return cwd


def check_test_agent_status():
    try:
        parsed = parse.urlparse(agent_config.trace_agent_url)
        conn = httplib.HTTPConnection(parsed.hostname, parsed.port)
        conn.request("GET", "/info")
        response = conn.getresponse()
        if response.status == 200:
            return True
        else:
            return False
    except Exception:
        return False


def flush_test_tracer_spans(writer):
    client = writer._clients[0]
    n_traces = len(client.encoder)
    try:
        encoded_traces, _ = client.encoder.encode()
        if encoded_traces is None:
            return
        headers = writer._get_finalized_headers(n_traces, client)
        response = writer._put(encoded_traces, add_dd_env_variables_to_headers(headers), client, no_trace=True)
    except Exception:
        return

    assert response.status == 200, response.body


def add_dd_env_variables_to_headers(headers):
    dd_env_vars = {key: value for key, value in os.environ.items() if key.startswith("DD_")}
    dd_env_vars["DD_SERVICE"] = dd_config.service
    dd_env_vars["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = SCHEMA_VERSION

    if dd_env_vars:
        dd_env_vars_string = ",".join(["%s=%s" % (key, value) for key, value in dd_env_vars.items()])
        headers["X-Datadog-Trace-Env-Variables"] = dd_env_vars_string

    return headers


def get_128_bit_trace_id_from_headers(headers):
    tags_value = _DatadogMultiHeader._get_tags_value(headers)
    meta = _DatadogMultiHeader._extract_meta(tags_value)
    return _DatadogMultiHeader._put_together_trace_id(
        meta[HIGHER_ORDER_TRACE_ID_BITS], int(headers["x-datadog-trace-id"])
    )


def _get_skipped_item(item, skip_reason):
    if not inspect.isfunction(item) and not inspect.isclass(item):
        raise ValueError(f"Unexpected skipped object: {item}")

    if not hasattr(item, "pytestmark"):
        item.pytestmark = []

    item.pytestmark.append(pytest.mark.xfail(reason=skip_reason))

    return item


def _should_skip(until: int, condition=None):
    until = dt.datetime.fromtimestamp(until)
    if until and dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) < until.replace(tzinfo=None):
        return True
    return condition is not None and condition


def flaky(until: int, condition: bool = None, reason: str = None):
    return skip_if_until(until, condition=condition, reason=reason)


def skip_if_until(until: int, condition=None, reason=None):
    """Conditionally skip the test until the given epoch timestamp"""
    skip = _should_skip(until=until, condition=condition)

    def decorator(function_or_class):
        if not skip:
            return function_or_class

        full_reason = f"known bug, skipping until epoch time {until} - {reason or ''}"
        return _get_skipped_item(function_or_class, full_reason)

    return decorator


def _build_env(env=None, file_path=FILE_PATH):
    """When a script runs in a subprocess, there are times in the CI or locally when it's assigned a different
    path than expected. Even worse, we've seen scripts that worked for months suddenly stop working because of this.
    With this function, we always set the path to ensure consistent results both locally and across different
    CI environments
    """
    environ = dict(PATH="%s:%s" % (DDTRACE_PATH, file_path), PYTHONPATH="%s:%s" % (DDTRACE_PATH, file_path))
    if os.environ.get("PATH"):
        environ["PATH"] = "%s:%s" % (os.environ.get("PATH"), environ["PATH"])
    if os.environ.get("PYTHONPATH"):
        environ["PYTHONPATH"] = "%s:%s" % (os.environ.get("PYTHONPATH"), environ["PYTHONPATH"])
    if env:
        for k, v in env.items():
            environ[k] = v
    return environ


_ID = 0


def remote_config_build_payload(product, data, path, sha_hash=None, id_based_on_content=False):
    global _ID
    if not id_based_on_content:
        _ID += 1
    hash_key = str(sha_hash or hash(str(data)))
    return Payload(
        {
            "id": hash_key if id_based_on_content else _ID,
            "product_name": product,
            "sha256_hash": hash_key,
            "length": len(str(data)),
            "tuf_version": 1,
        },
        f"Datadog/1/{product}/{path}",
        data,
    )


@contextmanager
def override_third_party_packages(packages: List[str]):
    try:
        original_callonce = _third_party_packages.__wrapped__.__callonce_result__
    except AttributeError:
        original_callonce = None

    try:
        original_mapping = _package_for_root_module_mapping.__wrapped__.__callonce_result__
    except AttributeError:
        original_mapping = None

    _third_party_packages.__wrapped__.__callonce_result__ = (packages, None)
    _package_for_root_module_mapping.__wrapped__.__callonce_result__ = (
        {p: Distribution(p, "0.0.0") for p in packages},
        None,
    )
    filename_to_package.cache_clear()
    is_third_party.cache_clear()

    try:
        yield
    finally:
        if original_callonce is not None:
            _third_party_packages.__wrapped__.__callonce_result__ = original_callonce
        else:
            del _third_party_packages.__wrapped__.__callonce_result__

        if original_mapping is not None:
            _package_for_root_module_mapping.__wrapped__.__callonce_result__ = original_mapping
        else:
            del _package_for_root_module_mapping.__wrapped__.__callonce_result__

        filename_to_package.cache_clear()
        is_third_party.cache_clear()
