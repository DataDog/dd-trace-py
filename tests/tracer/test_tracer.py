# -*- coding: utf-8 -*-
"""
tests for Tracer and utilities.
"""

import contextlib
import gc
import logging
from os import getpid
import threading
from unittest.case import SkipTest

import mock
import pytest

import ddtrace
from ddtrace.constants import _HOSTNAME_KEY
from ddtrace.constants import _ORIGIN_KEY
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import AUTO_REJECT
from ddtrace.constants import ENV_KEY
from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.constants import PID
from ddtrace.constants import USER_KEEP
from ddtrace.constants import USER_REJECT
from ddtrace.constants import VERSION_KEY
from ddtrace.contrib.internal.trace_utils import set_user
from ddtrace.ext import user
import ddtrace.internal
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.rate_limiter import RateLimiter
from ddtrace.internal.serverless import has_aws_lambda_agent_extension
from ddtrace.internal.serverless import in_aws_lambda
from ddtrace.internal.writer import AgentWriter
from ddtrace.internal.writer import LogWriter
from ddtrace.settings._config import Config
from ddtrace.trace import Context
from ddtrace.trace import tracer as global_tracer
from tests.subprocesstest import run_in_subprocess
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import override_global_config

from ..utils import override_env


class TracerTestCases(TracerTestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, tracer, caplog):
        self._caplog = caplog
        self._tracer_appsec = tracer

    def test_tracer_vars(self):
        span = self.trace("a", service="s", resource="r", span_type="t")
        span.assert_matches(name="a", service="s", resource="r", span_type="t")
        # DEV: Finish to ensure we don't leak `service` between spans
        span.finish()

        span = self.trace("a")
        span.assert_matches(name="a", resource="a", span_type=None)
        span.finish()

    def test_tracer(self):
        def _mix():
            with self.trace("cake.mix"):
                pass

        def _bake():
            with self.trace("cake.bake"):
                pass

        def _make_cake():
            with self.trace("cake.make") as span:
                span.service = "baker"
                span.resource = "cake"
                _mix()
                _bake()

        # let's run it and make sure all is well.
        self.assert_has_no_spans()
        _make_cake()

        # Capture root's trace id to assert later
        root_trace_id = self.get_root_span().trace_id

        # Assert structure of this trace
        self.assert_structure(
            # Root span with 2 children
            dict(name="cake.make", resource="cake", service="baker", parent_id=None),
            (
                # Span with no children
                dict(name="cake.mix", resource="cake.mix", service="baker"),
                # Span with no children
                dict(name="cake.bake", resource="cake.bake", service="baker"),
            ),
        )

        # do it again and make sure it has new trace ids
        self.reset()
        _make_cake()
        self.assert_span_count(3)
        for s in self.spans:
            assert s.trace_id != root_trace_id

    def test_tracer_wrap(self):
        @self.tracer.wrap("decorated_function", service="s", resource="r", span_type="t")
        def f(tag_name, tag_value):
            # make sure we can still set tags
            span = self.tracer.current_span()
            span.set_tag(tag_name, tag_value)

        f("a", "b")

        self.assert_span_count(1)
        span = self.get_root_span()
        span.assert_matches(
            name="decorated_function",
            service="s",
            resource="r",
            span_type="t",
            meta=dict(a="b"),
        )

    def test_tracer_pid(self):
        with self.trace("root") as root_span:
            with self.trace("child") as child_span:
                pass

        # Root span should contain the pid of the current process
        root_span.assert_metrics({PID: getpid()}, exact=False)

        # Child span should not contain a pid tag
        child_span.assert_metrics(dict(), exact=True)

    def test_tracer_wrap_default_name(self):
        @self.tracer.wrap()
        def f():
            pass

        f()

        self.assert_structure(dict(name="tests.tracer.test_tracer.f"))

    def test_tracer_wrap_exception(self):
        @self.tracer.wrap()
        def f():
            raise Exception("bim")

        with self.assertRaises(Exception) as ex:
            f()

            self.assert_structure(
                dict(
                    name="tests.test_tracer.f",
                    error=1,
                    meta={
                        "error.message": ex.message,
                        "error.type": ex.__class__.__name__,
                    },
                ),
            )

    def test_tracer_wrap_multiple_calls(self):
        @self.tracer.wrap()
        def f():
            pass

        f()
        f()

        self.assert_span_count(2)
        assert self.spans[0].span_id != self.spans[1].span_id

    def test_tracer_wrap_span_nesting_current_root_span(self):
        @self.tracer.wrap("inner")
        def inner():
            root_span = self.tracer.current_root_span()
            self.assertEqual(root_span.name, "outer")

        @self.tracer.wrap("outer")
        def outer():
            root_span = self.tracer.current_root_span()
            self.assertEqual(root_span.name, "outer")

            with self.trace("mid"):
                root_span = self.tracer.current_root_span()
                self.assertEqual(root_span.name, "outer")

                inner()

        outer()

    def test_tracer_wrap_span_nesting(self):
        @self.tracer.wrap("inner")
        def inner():
            pass

        @self.tracer.wrap("outer")
        def outer():
            with self.trace("mid"):
                inner()

        outer()

        self.assert_span_count(3)
        self.assert_structure(
            dict(name="outer"),
            ((dict(name="mid"), (dict(name="inner"),)),),
        )

    def test_tracer_wrap_class(self):
        class Foo(object):
            @staticmethod
            @self.tracer.wrap()
            def s():
                return 1

            @classmethod
            @self.tracer.wrap()
            def c(cls):
                return 2

            @self.tracer.wrap()
            def i(cls):
                return 3

        f = Foo()
        self.assertEqual(f.s(), 1)
        self.assertEqual(f.c(), 2)
        self.assertEqual(f.i(), 3)

        self.assert_span_count(3)
        self.spans[0].assert_matches(name="tests.tracer.test_tracer.s")
        self.spans[1].assert_matches(name="tests.tracer.test_tracer.c")
        self.spans[2].assert_matches(name="tests.tracer.test_tracer.i")

    def test_tracer_wrap_factory(self):
        def wrap_executor(tracer, fn, args, kwargs, span_name=None, service=None, resource=None, span_type=None):
            with tracer.trace("wrap.overwrite") as span:
                span.set_tag("args", args)
                span.set_tag("kwargs", kwargs)
                return fn(*args, **kwargs)

        @self.tracer.wrap()
        def wrapped_function(param, kw_param=None):
            self.assertEqual(42, param)
            self.assertEqual(42, kw_param)

        # set the custom wrap factory after the wrapper has been called
        self.tracer._wrap_executor = wrap_executor

        # call the function expecting that the custom tracing wrapper is used
        wrapped_function(42, kw_param=42)

        self.assert_span_count(1)
        self.spans[0].assert_matches(
            name="wrap.overwrite",
            meta=dict(args="(42,)", kwargs="{'kw_param': 42}"),
        )

    def test_tracer_wrap_factory_nested(self):
        def wrap_executor(tracer, fn, args, kwargs, span_name=None, service=None, resource=None, span_type=None):
            with tracer.trace("wrap.overwrite") as span:
                span.set_tag("args", args)
                span.set_tag("kwargs", kwargs)
                return fn(*args, **kwargs)

        @self.tracer.wrap()
        def wrapped_function(param, kw_param=None):
            self.assertEqual(42, param)
            self.assertEqual(42, kw_param)

        # set the custom wrap factory after the wrapper has been called
        self.tracer._wrap_executor = wrap_executor

        # call the function expecting that the custom tracing wrapper is used
        with self.trace("wrap.parent", service="webserver"):
            wrapped_function(42, kw_param=42)

        self.assert_structure(
            dict(name="wrap.parent", service="webserver"),
            (dict(name="wrap.overwrite", service="webserver", meta=dict(args="(42,)", kwargs="{'kw_param': 42}")),),
        )

    def test_tracer_wrap_generator(self):
        @self.tracer.wrap("decorated_generator", service="s", resource="r", span_type="t")
        def f(tag_name, tag_value):
            # make sure we can still set tags
            span = self.tracer.current_span()
            span.set_tag(tag_name, tag_value)

            for i in range(3):
                yield i

        result = list(f("a", "b"))
        assert result == [0, 1, 2]

        self.assert_span_count(1)
        span = self.get_root_span()
        span.assert_matches(
            name="decorated_generator",
            service="s",
            resource="r",
            span_type="t",
            meta=dict(a="b"),
        )

    def test_tracer_disabled(self):
        self.tracer.enabled = True
        with self.trace("foo") as s:
            s.set_tag("a", "b")

        self.assert_has_spans()
        self.reset()

        self.tracer.enabled = False
        with self.trace("foo") as s:
            s.set_tag("a", "b")
        self.assert_has_no_spans()

    def test_unserializable_span_with_finish(self):
        try:
            import numpy as np
        except ImportError:
            raise SkipTest("numpy not installed")

        # a weird case where manually calling finish with an unserializable
        # span was causing an loop of serialization.
        with self.trace("parent") as span:
            span._metrics["as"] = np.int64(1)  # circumvent the data checks
            span.finish()

    def test_tracer_disabled_mem_leak(self):
        # ensure that if the tracer is disabled, we still remove things from the
        # span buffer upon finishing.
        self.tracer.enabled = False
        s1 = self.trace("foo")
        s1.finish()

        p1 = self.tracer.current_span()
        s2 = self.trace("bar")

        self.assertIsNone(s2._parent)
        s2.finish()
        self.assertIsNone(p1)

    def test_tracer_global_tags(self):
        s1 = self.trace("brie")
        s1.finish()
        self.assertIsNone(s1.get_tag("env"))
        self.assertIsNone(s1.get_tag("other"))

        self.tracer.set_tags({"env": "prod"})
        s2 = self.trace("camembert")
        s2.finish()
        self.assertEqual(s2.get_tag("env"), "prod")
        self.assertIsNone(s2.get_tag("other"))

        self.tracer.set_tags({"env": "staging", "other": "tag"})
        s3 = self.trace("gruyere")
        s3.finish()
        self.assertEqual(s3.get_tag("env"), "staging")
        self.assertEqual(s3.get_tag("other"), "tag")

    def test_global_context(self):
        # the tracer uses a global thread-local Context
        with self.trace("fake_span") as span:
            ctx = self.tracer.current_trace_context()
            assert ctx.trace_id == span.trace_id
            assert ctx.span_id == span.span_id
            assert ctx._is_remote is False

    def test_tracer_current_span(self):
        # the current span is in the local Context()
        with self.trace("fake_span") as span:
            assert self.tracer.current_span() == span

        with self.trace("fake_span") as span:
            assert self.tracer.current_span() == span

    def test_tracer_current_span_missing_context(self):
        self.assertIsNone(self.tracer.current_span())

    def test_tracer_current_root_span_missing_context(self):
        self.assertIsNone(self.tracer.current_root_span())

    def test_default_provider_get(self):
        ctx = self.tracer.context_provider.active()
        assert ctx is None

    def test_default_provider_set(self):
        # The Context Provider can set the current active Context;
        # this could happen in distributed tracing
        ctx = Context(trace_id=42, span_id=100)
        self.tracer.context_provider.activate(ctx)
        with self.trace("web.request") as span:
            span.assert_matches(name="web.request", trace_id=42, parent_id=100)

    def test_start_span(self):
        # it should create a root Span
        with self.tracer.start_span("web.request") as span:
            assert span.name == "web.request"
            assert span.parent_id is None
            span.finish()
            spans = self.pop_spans()
            assert len(spans) == 1
            assert spans[0] is span

    def test_start_span_optional(self):
        # it should create a root Span with arguments
        with self.start_span("web.request", service="web", resource="/", span_type="http") as span:
            pass
        span.assert_matches(
            name="web.request",
            service="web",
            resource="/",
            span_type="http",
        )

    def test_start_span_service_default(self):
        span = self.start_span("")
        span.assert_matches(service="tests.tracer")
        span.finish()

    def test_start_span_service_from_parent(self):
        with self.start_span("parent", service="mysvc") as parent:
            with self.start_span("child", child_of=parent) as child:
                pass
        child.assert_matches(
            name="child",
            service="mysvc",
        )

    def test_start_span_service_global_config(self):
        # When no service is provided a default
        with self.override_global_config(dict(service="mysvc")):
            with self.start_span("") as span:
                span.assert_matches(service="mysvc")

    def test_start_span_service_global_config_parent(self):
        # Parent should have precedence over global config
        with self.override_global_config(dict(service="mysvc")):
            with self.start_span("parent", service="parentsvc") as parent:
                with self.start_span("child", child_of=parent) as child:
                    pass
        child.assert_matches(
            name="child",
            service="parentsvc",
        )

    def test_start_child_span(self):
        # it should create a child Span for the given parent
        with self.start_span("web.request") as parent:
            assert self.tracer.current_span() is None
            with self.start_span("web.worker", child_of=parent) as child:
                assert self.tracer.current_span() is None

        parent.assert_matches(
            name="web.request",
            parent_id=None,
            _parent=None,
        )
        child.assert_matches(
            name="web.worker",
            parent_id=parent.span_id,
            _parent=parent,
        )

    def test_start_child_span_attributes(self):
        # it should create a child Span with parent's attributes
        with self.start_span("web.request", service="web", resource="/", span_type="http") as parent:
            with self.start_span("web.worker", child_of=parent) as child:
                child.assert_matches(name="web.worker", service="web")

    def test_start_child_from_context(self):
        # it should create a child span with a populated Context
        with self.start_span("web.request") as root:
            with self.start_span("web.worker", child_of=root.context) as child:
                pass
        child.assert_matches(
            name="web.worker",
            parent_id=root.span_id,
            trace_id=root.trace_id,
            _parent=None,
        )

    def test_tracer_set_user(self):
        with self.trace("fake_span") as span:
            set_user(
                self.tracer,
                user_id="usr.id",
                email="usr.email",
                name="usr.name",
                session_id="usr.session_id",
                role="usr.role",
                scope="usr.scope",
            )
            assert span.get_tag(user.ID)
            assert span.get_tag(user.EMAIL)
            assert span.get_tag(user.SESSION_ID)
            assert span.get_tag(user.NAME)
            assert span.get_tag(user.ROLE)
            assert span.get_tag(user.SCOPE)
            assert span.context.dd_user_id is None

    def test_tracer_set_user_in_span(self):
        with self.trace("root_span") as parent_span:
            with self.trace("user_span") as user_span:
                user_span.parent_id = parent_span.span_id
                set_user(
                    self.tracer,
                    user_id="usr.id",
                    email="usr.email",
                    name="usr.name",
                    session_id="usr.session_id",
                    role="usr.role",
                    scope="usr.scope",
                    span=user_span,
                )
                assert user_span.get_tag(user.ID) and parent_span.get_tag(user.ID) is None
                assert user_span.get_tag(user.EMAIL) and parent_span.get_tag(user.EMAIL) is None
                assert user_span.get_tag(user.SESSION_ID) and parent_span.get_tag(user.SESSION_ID) is None
                assert user_span.get_tag(user.NAME) and parent_span.get_tag(user.NAME) is None
                assert user_span.get_tag(user.ROLE) and parent_span.get_tag(user.ROLE) is None
                assert user_span.get_tag(user.SCOPE) and parent_span.get_tag(user.SCOPE) is None
                assert user_span.context.dd_user_id is None

    def test_tracer_set_user_mandatory(self):
        with self.trace("fake_span") as span:
            set_user(
                self.tracer,
                user_id="usr.id",
            )
            span_keys = list(span.get_tags().keys())
            span_keys.sort()
            assert span_keys == ["runtime-id", "usr.id"]
            assert span.get_tag(user.ID)
            assert span.get_tag(user.EMAIL) is None
            assert span.get_tag(user.SESSION_ID) is None
            assert span.get_tag(user.NAME) is None
            assert span.get_tag(user.ROLE) is None
            assert span.get_tag(user.SCOPE) is None
            assert span.context.dd_user_id is None

    def test_tracer_set_user_warning_no_span(self):
        with self._caplog.at_level(logging.WARNING):
            set_user(
                self.tracer,
                user_id="usr.id",
            )
            assert "No root span in the current execution. Skipping set_user tags" in self._caplog.records[0].message

    def test_tracer_set_user_propagation(self):
        with self.trace("fake_span") as span:
            user_id_string = "usr.id"
            set_user(
                self.tracer,
                user_id=user_id_string,
                email="usr.email",
                name="usr.name",
                session_id="usr.session_id",
                role="usr.role",
                scope="usr.scope",
                propagate=True,
            )
            user_id = span.context._meta.get("_dd.p.usr.id")

            assert span.get_tag(user.ID) == user_id_string
            assert span.context.dd_user_id == user_id_string
            assert user_id == "dXNyLmlk"

    def test_tracer_set_user_propagation_empty(self):
        with self.trace("fake_span") as span:
            user_id_string = ""
            set_user(
                self.tracer,
                user_id=user_id_string,
                email="usr.email",
                name="usr.name",
                session_id="usr.session_id",
                role="usr.role",
                scope="usr.scope",
                propagate=True,
            )
            user_id = span.context._meta.get("_dd.p.usr.id")

            assert span.get_tag(user.ID) is None
            assert span.context.dd_user_id is None
            assert not user_id

    def test_tracer_set_user_propagation_string_error(self):
        with self.trace("fake_span") as span:
            user_id_string = "ユーザーID"
            set_user(
                self.tracer,
                user_id=user_id_string,
                email="usr.email",
                name="usr.name",
                session_id="usr.session_id",
                role="usr.role",
                scope="usr.scope",
                propagate=True,
            )
            user_id = span.context._meta.get("_dd.p.usr.id")

            assert span.get_tag(user.ID) == user_id_string
            assert span.context.dd_user_id == user_id_string
            assert user_id == "44Om44O844K244O8SUQ="


@pytest.mark.subprocess(env=dict(DD_AGENT_PORT=None, DD_AGENT_HOST=None, DD_TRACE_AGENT_URL=None))
def test_tracer_url_default():
    import ddtrace

    assert ddtrace.trace.tracer._span_aggregator.writer.agent_url == "http://localhost:8126"


@pytest.mark.subprocess()
def test_tracer_shutdown_no_timeout():
    import mock

    from ddtrace.internal.writer import AgentWriter
    from ddtrace.trace import tracer as t

    with mock.patch.object(AgentWriter, "stop") as mock_stop:
        with mock.patch.object(AgentWriter, "join") as mock_join:
            t.shutdown()

    mock_stop.assert_called()
    mock_join.assert_not_called()


@pytest.mark.subprocess()
def test_tracer_shutdown_timeout():
    import mock

    from ddtrace.internal.writer import AgentWriter
    from ddtrace.trace import tracer as t

    with mock.patch.object(AgentWriter, "stop") as mock_stop:
        with t.trace("something"):
            pass

        t.shutdown(timeout=2)
    mock_stop.assert_called_once_with(2)


@pytest.mark.subprocess(
    err=b"Spans started after the tracer has been shut down will not be sent to the Datadog Agent.\n",
)
def test_tracer_shutdown():
    import mock

    from ddtrace.internal.writer import AgentWriter
    from ddtrace.trace import tracer as t

    t.shutdown()

    with mock.patch.object(AgentWriter, "write") as mock_write:
        with t.trace("something"):
            pass

    mock_write.assert_not_called()


@pytest.mark.skip(reason="Fails to Pickle RateLimiter in the Tracer")
@pytest.mark.subprocess
def test_tracer_fork():
    import contextlib
    import multiprocessing

    from ddtrace.trace import tracer as t

    original_pid = t._pid
    original_writer = t._span_aggregator.writer

    @contextlib.contextmanager
    def capture_failures(errors):
        try:
            yield
        except AssertionError as e:
            errors.put(e)

    def task(t, errors):
        # Start a new span to trigger process checking
        with t.trace("test", service="test"):
            # Assert we recreated the writer and have a new queue
            with capture_failures(errors):
                assert t._pid != original_pid
                assert t.writer is not original_writer
                assert t.writer._encoder is not original_writer._encoder

        # Assert the trace got written into the correct queue
        assert len(original_writer._encoder) == 0
        assert len(t.writer._encoder) == 1

    # Assert tracer in a new process correctly recreates the writer
    errors = multiprocessing.Queue()
    p = multiprocessing.Process(target=task, args=(t, errors))
    try:
        p.start()
    finally:
        p.join(timeout=2)

    assert errors.empty(), errors.get()

    # Ensure writing into the tracer in this process still works as expected
    with t.trace("test", service="test"):
        assert t._pid == original_pid
        assert t._span_aggregator.writer == original_writer
        assert t._span_aggregator.writer._encoder == original_writer._encoder

    # Assert the trace got written into the correct queue
    assert len(original_writer._encoder) == 1
    assert len(t._span_aggregator.writer._encoder) == 1


def test_tracer_with_version():
    t = DummyTracer()

    # With global `config.version` defined
    with override_global_config(dict(version="1.2.3")):
        with t.trace("test.span") as span:
            assert span.get_tag(VERSION_KEY) == "1.2.3"

            # override manually
            span.set_tag(VERSION_KEY, "4.5.6")
            assert span.get_tag(VERSION_KEY) == "4.5.6"

    # With no `config.version` defined
    with t.trace("test.span") as span:
        assert span.get_tag(VERSION_KEY) is None

        # explicitly set in the span
        span.set_tag(VERSION_KEY, "1.2.3")
        assert span.get_tag(VERSION_KEY) == "1.2.3"

    # With global tags set
    t.set_tags({VERSION_KEY: "tags.version"})
    with override_global_config(dict(version="config.version")):
        with t.trace("test.span") as span:
            assert span.get_tag(VERSION_KEY) == "config.version"


def test_tracer_with_env():
    t = DummyTracer()

    # With global `config.env` defined
    with override_global_config(dict(env="prod")):
        with t.trace("test.span") as span:
            assert span.get_tag(ENV_KEY) == "prod"

            # override manually
            span.set_tag(ENV_KEY, "prod-staging")
            assert span.get_tag(ENV_KEY) == "prod-staging"

    # With no `config.env` defined
    with t.trace("test.span") as span:
        assert span.get_tag(ENV_KEY) is None

        # explicitly set in the span
        span.set_tag(ENV_KEY, "prod-staging")
        assert span.get_tag(ENV_KEY) == "prod-staging"

    # With global tags set
    t.set_tags({ENV_KEY: "tags.env"})
    with override_global_config(dict(env="config.env")):
        with t.trace("test.span") as span:
            assert span.get_tag(ENV_KEY) == "config.env"


class EnvTracerTestCase(TracerTestCase):
    """Tracer test cases requiring environment variables."""

    @run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_service_name_env(self):
        with self.start_span("") as span:
            pass
        span.assert_matches(
            service="mysvc",
        )

    @run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_service_name_env_global_config(self):
        # Global config should have higher precedence than the environment variable
        with self.override_global_config(dict(service="overridesvc")):
            with self.start_span("") as span:
                pass
        span.assert_matches(
            service="overridesvc",
        )

    @run_in_subprocess(env_overrides=dict(DD_VERSION="0.1.2"))
    def test_version_no_global_service(self):
        # Version should be set if no service name is present
        with self.trace("") as span:
            span.assert_matches(
                meta={
                    VERSION_KEY: "0.1.2",
                },
            )

        # The version will not be tagged if the service is not globally
        # configured.
        with self.trace("root", service="rootsvc") as root:
            assert VERSION_KEY not in root.get_tags()
            with self.trace("child") as span:
                assert VERSION_KEY not in span.get_tags()

    @run_in_subprocess(env_overrides=dict(DD_SERVICE="django", DD_VERSION="0.1.2"))
    def test_version_service(self):
        # Fleshed out example of service and version tagging

        # Our app is called django, we provide DD_SERVICE=django and DD_VERSION=0.1.2

        with self.trace("django.request") as root:
            # Root span should be tagged
            assert root.service == "django"
            assert VERSION_KEY in root.get_tags() and root.get_tag(VERSION_KEY) == "0.1.2"

            # Child spans should be tagged
            with self.trace("") as child1:
                assert child1.service == "django"
                assert VERSION_KEY in child1.get_tags() and child1.get_tag(VERSION_KEY) == "0.1.2"

            # Version should not be applied to spans of a service that isn't user-defined
            with self.trace("mysql.query", service="mysql") as span:
                assert VERSION_KEY not in span.get_tags()
                # Child should also not have a version
                with self.trace("") as child2:
                    assert child2.service == "mysql"
                    assert VERSION_KEY not in child2.get_tags()

    @run_in_subprocess(env_overrides=dict(DD_SERVICE="django", DD_VERSION="0.1.2", DD_SERVICE_MAPPING="mysql:django"))
    def test_version_service_mapping(self):
        """When using DD_SERVICE_MAPPING we properly add version tag to appropriate spans"""

        # Our app is called django, we provide DD_SERVICE=django and DD_VERSION=0.1.2
        # mysql spans will get remapped to django via DD_SERVICE_MAPPING=mysql:django

        with self.trace("django.request") as root:
            # Root span should be tagged
            assert root.service == "django"
            assert VERSION_KEY in root.get_tags() and root.get_tag(VERSION_KEY) == "0.1.2"

            # Child spans should be tagged
            with self.trace("") as child1:
                assert child1.service == "django"
                assert VERSION_KEY in child1.get_tags() and child1.get_tag(VERSION_KEY) == "0.1.2"

            # Service name gets remapped to django and we get version tag applied
            with self.trace("mysql.query", service="mysql") as span:
                assert span.service == "django"
                assert VERSION_KEY in span.get_tags() and span.get_tag(VERSION_KEY) == "0.1.2"
                # Child should also have a version
                with self.trace("") as child2:
                    assert child2.service == "django"
                    assert VERSION_KEY in child2.get_tags() and child2.get_tag(VERSION_KEY) == "0.1.2"

    @run_in_subprocess(
        env_overrides=dict(
            AWS_LAMBDA_FUNCTION_NAME="my-func", DD_AGENT_HOST="", DD_TRACE_AGENT_URL="", DATADOG_TRACE_AGENT_HOSTNAME=""
        )
    )
    def test_detect_agentless_env_with_lambda(self):
        assert in_aws_lambda()
        assert not has_aws_lambda_agent_extension()
        assert isinstance(ddtrace.tracer._span_aggregator.writer, LogWriter)

    @run_in_subprocess(env_overrides=dict(AWS_LAMBDA_FUNCTION_NAME="my-func", DD_AGENT_HOST="localhost"))
    def test_detect_agent_config(self):
        assert isinstance(global_tracer._span_aggregator.writer, AgentWriter)

    @run_in_subprocess(env_overrides=dict(DD_TAGS="key1:value1,key2:value2"))
    def test_dd_tags(self):
        assert self.tracer._tags.get("key1") == "value1"
        assert self.tracer._tags.get("key2") == "value2"

    @run_in_subprocess(env_overrides=dict(DD_TAGS="key1:value1,key2:value2, key3"))
    def test_dd_tags_invalid(self):
        assert self.tracer._tags.get("key1")
        assert self.tracer._tags.get("key2")
        assert not self.tracer._tags.get("key3")

    @run_in_subprocess(env_overrides=dict(DD_TAGS="service:mysvc,env:myenv,version:myvers"))
    def test_tags_from_DD_TAGS(self):
        t = DummyTracer()
        with t.trace("test") as s:
            assert s.service == "mysvc"
            assert s.get_tag("env") == "myenv"
            assert s.get_tag("version") == "myvers"

    @run_in_subprocess(
        env_overrides=dict(
            DD_TAGS="service:s,env:e,version:v",
            DD_ENV="env",
            DD_SERVICE="svc",
            DD_VERSION="0.123",
        )
    )
    def test_tags_from_DD_TAGS_precedence(self):
        with global_tracer.trace("test") as s:
            assert s.service == "svc"
            assert s.get_tag("env") == "env"
            assert s.get_tag("version") == "0.123"

    @run_in_subprocess(env_overrides=dict(DD_TAGS="service:mysvc,env:myenv,version:myvers"))
    def test_tags_from_DD_TAGS_override(self):
        ddtrace.config.env = "env"
        ddtrace.config.service = "service"
        ddtrace.config.version = "0.123"
        with global_tracer.trace("test") as s:
            assert s.service == "service"
            assert s.get_tag("env") == "env"
            assert s.get_tag("version") == "0.123"


def test_tracer_set_runtime_tags():
    with global_tracer.start_span("foobar") as span:
        pass

    assert len(span.get_tag("runtime-id"))

    with global_tracer.start_span("foobaz") as span2:
        pass

    assert span.get_tag("runtime-id") == span2.get_tag("runtime-id")


def _test_tracer_runtime_tags_fork_task(tracer, q):
    span = tracer.start_span("foobaz")
    q.put(span.get_tag("runtime-id"))
    span.finish()


@pytest.mark.skip(reason="Fails to Pickle RateLimiter in the Tracer")
@pytest.mark.subprocess
def test_tracer_runtime_tags_fork():
    import multiprocessing

    from ddtrace.trace import tracer
    from tests.tracer.test_tracer import _test_tracer_runtime_tags_fork_task

    span = tracer.start_span("foobar")
    span.finish()

    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=_test_tracer_runtime_tags_fork_task, args=(tracer, q))
    p.start()
    p.join(60)

    children_tag = q.get()
    assert children_tag != span.get_tag("runtime-id")


def test_tracer_runtime_tags_cross_execution(tracer):
    ctx = Context(trace_id=12, span_id=21)
    tracer.context_provider.activate(ctx)
    with tracer.trace("span") as span:
        pass
    assert span.get_tag("runtime-id") is not None
    assert span.get_metric(PID) is not None


def test_start_span_hooks():
    t = DummyTracer()

    result = {}

    @t.on_start_span
    def store_span(span):
        result["span"] = span

    span = t.start_span("hello")

    assert span == result["span"]
    span.finish()


def test_deregister_start_span_hooks():
    t = DummyTracer()

    result = {}

    @t.on_start_span
    def store_span(span):
        result["span"] = span

    t.deregister_on_start_span(store_span)

    with t.start_span("hello"):
        pass

    assert result == {}


@pytest.mark.subprocess(parametrize={"DD_TRACE_ENABLED": ["true", "false"]})
def test_enable():
    import os

    from ddtrace.trace import tracer as t2

    if os.environ["DD_TRACE_ENABLED"] == "true":
        assert t2.enabled
    else:
        assert not t2.enabled


@pytest.mark.subprocess(
    err=b"Shutting down tracer with 2 unfinished spans. "
    b"Unfinished spans will not be sent to Datadog: "
    b"trace_id=123 parent_id=0 span_id=456 name=unfinished_span1 "
    b"resource=my_resource1 started=46121775360.0 sampling_priority=2, "
    b"trace_id=123 parent_id=456 span_id=666 name=unfinished_span2 "
    b"resource=my_resource1 started=167232131231.0 sampling_priority=2\n"
)
def test_unfinished_span_warning_log():
    """Test that a warning log is emitted when the tracer is shut down with unfinished spans."""
    import ddtrace.auto  # noqa

    from ddtrace.constants import MANUAL_KEEP_KEY
    from ddtrace.trace import tracer

    # Create two unfinished spans
    span1 = tracer.trace("unfinished_span1", service="my_service", resource="my_resource1")
    span2 = tracer.trace("unfinished_span2", service="my_service", resource="my_resource1")
    # hardcode the trace_id, parent_id, span_id, sampling decision and start time to make the test deterministic
    span1.trace_id = 123
    span1.parent_id = 0
    span1.span_id = 456
    span1.start = 46121775360
    span1.set_tag(MANUAL_KEEP_KEY)
    span2.trace_id = 123
    span2.parent_id = 456
    span2.span_id = 666
    span2.start = 167232131231
    span2.set_tag(MANUAL_KEEP_KEY)


@pytest.mark.subprocess(parametrize={"DD_TRACE_ENABLED": ["true", "false"]})
def test_threaded_import():
    import threading

    def thread_target():
        import ddtrace  # noqa: F401

    t = threading.Thread(target=thread_target)
    t.start()
    t.join(60)


def test_runtime_id_parent_only():
    tracer = DummyTracer()

    # Parent spans should have runtime-id
    with tracer.trace("test") as s:
        rtid = s.get_tag("runtime-id")
        assert isinstance(rtid, str)

        # Child spans should not
        with tracer.trace("test2") as s2:
            assert s2.get_tag("runtime-id") is None

    # Parent spans should have runtime-id
    s = tracer.trace("test")
    s.finish()
    rtid = s.get_tag("runtime-id")
    assert isinstance(rtid, str)


@pytest.mark.skipif(
    PYTHON_VERSION_INFO >= (3, 12),
    reason="This test runs in a multithreaded process, using os.fork() may cause deadlocks in child processes",
)
@pytest.mark.subprocess
def test_runtime_id_fork():
    import os

    from ddtrace.trace import tracer

    s = tracer.trace("test")
    s.finish()

    rtid = s.get_tag("runtime-id")
    assert isinstance(rtid, str)

    pid = os.fork()

    if pid == 0:
        # child
        s = tracer.trace("test")
        s.finish()

        rtid_child = s.get_tag("runtime-id")
        assert isinstance(rtid_child, str)
        assert rtid != rtid_child
        os._exit(12)

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


def test_filters(tracer, test_spans):
    class FilterAll(object):
        def process_trace(self, trace):
            return None

    tracer.configure(trace_processors=[FilterAll()])

    with tracer.trace("root"):
        with tracer.trace("child"):
            pass

    spans = test_spans.pop()
    assert len(spans) == 0

    class FilterMutate(object):
        def __init__(self, key, value):
            self.key = key
            self.value = value

        def process_trace(self, trace):
            for s in trace:
                s.set_tag(self.key, self.value)
            return trace

    tracer.configure(trace_processors=[FilterMutate("boop", "beep")])

    with tracer.trace("root"):
        with tracer.trace("child"):
            pass

    spans = test_spans.pop()
    assert len(spans) == 2
    s1, s2 = spans
    assert s1.get_tag("boop") == "beep"
    assert s2.get_tag("boop") == "beep"

    # Test multiple filters
    tracer.configure(trace_processors=[FilterMutate("boop", "beep"), FilterMutate("mats", "sundin")])

    with tracer.trace("root"):
        with tracer.trace("child"):
            pass

    spans = test_spans.pop()
    assert len(spans) == 2
    for s in spans:
        assert s.get_tag("boop") == "beep"
        assert s.get_tag("mats") == "sundin"

    class FilterBroken(object):
        def process_trace(self, trace):
            _ = 1 / 0

    tracer.configure(trace_processors=[FilterBroken()])

    with tracer.trace("root"):
        with tracer.trace("child"):
            pass

    spans = test_spans.pop()
    assert len(spans) == 2

    tracer.configure(trace_processors=[FilterMutate("boop", "beep"), FilterBroken()])
    with tracer.trace("root"):
        with tracer.trace("child"):
            pass

    spans = test_spans.pop()
    assert len(spans) == 2
    for s in spans:
        assert s.get_tag("boop") == "beep"


def test_early_exit(tracer, test_spans):
    s1 = tracer.trace("1")
    s2 = tracer.trace("2")
    with mock.patch.object(logging.Logger, "debug") as mock_logger:
        s1.finish()
        s2.finish()

    calls = [
        mock.call("span %r closing after its parent %r, this is an error when not using async", s2, s1),
    ]
    mock_logger.assert_has_calls(calls)
    assert s1.parent_id is None
    assert s2.parent_id is s1.span_id

    traces = test_spans.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 2

    s1 = tracer.trace("1-1")
    s1.finish()
    assert s1.parent_id is None

    s1 = tracer.trace("1-2")
    s1.finish()
    assert s1.parent_id is None


class TestPartialFlush(TracerTestCase):
    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_TRACE_PARTIAL_FLUSH_ENABLED="true", DD_TRACE_PARTIAL_FLUSH_MIN_SPANS="5")
    )
    def test_partial_flush(self):
        root = self.tracer.trace("root")
        for i in range(5):
            self.tracer.trace("child%s" % i).finish()

        traces = self.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 5
        assert [s.name for s in traces[0]] == ["child0", "child1", "child2", "child3", "child4"]

        root.finish()
        traces = self.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        assert traces[0][0].name == "root"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_TRACE_PARTIAL_FLUSH_ENABLED="true", DD_TRACE_PARTIAL_FLUSH_MIN_SPANS="1")
    )
    def test_partial_flush_too_many(self):
        root = self.tracer.trace("root")
        for i in range(5):
            self.tracer.trace("child%s" % i).finish()

        traces = self.pop_traces()
        assert len(traces) == 5
        for t in traces:
            assert len(t) == 1
        assert [t[0].name for t in traces] == ["child0", "child1", "child2", "child3", "child4"]
        for t in traces:
            assert t[0].parent_id == root.span_id

        root.finish()
        traces = self.pop_traces()
        assert len(traces) == 1
        assert traces[0][0].name == "root"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_TRACE_PARTIAL_FLUSH_ENABLED="true", DD_TRACE_PARTIAL_FLUSH_MIN_SPANS="6")
    )
    def test_partial_flush_too_few(self):
        root = self.tracer.trace("root")
        for i in range(5):
            self.tracer.trace("child%s" % i).finish()

        traces = self.pop_traces()
        assert len(traces) == 0
        root.finish()
        traces = self.pop_traces()
        assert len(traces) == 1
        assert [s.name for s in traces[0]] == ["root", "child0", "child1", "child2", "child3", "child4"]


def test_unicode_config_vals():
    with override_global_config(dict(version="😇", env="😇")):
        with global_tracer.trace("1"):
            pass
    global_tracer.flush()


def test_ctx(tracer, test_spans):
    with tracer.trace("test") as s1:
        assert tracer.current_span() == s1
        assert tracer.current_root_span() == s1
        assert tracer.current_trace_context().trace_id == s1.trace_id
        assert tracer.current_trace_context().span_id == s1.span_id

        with tracer.trace("test2") as s2:
            assert tracer.current_span() == s2
            assert tracer.current_root_span() == s1
            assert tracer.current_trace_context().trace_id == s1.trace_id
            assert tracer.current_trace_context().span_id == s2.span_id

            with tracer.trace("test3") as s3:
                assert tracer.current_span() == s3
                assert tracer.current_root_span() == s1
                assert tracer.current_trace_context().trace_id == s1.trace_id
                assert tracer.current_trace_context().span_id == s3.span_id

            assert tracer.current_trace_context().trace_id == s1.trace_id
            assert tracer.current_trace_context().span_id == s2.span_id

        with tracer.trace("test4") as s4:
            assert tracer.current_span() == s4
            assert tracer.current_root_span() == s1
            assert tracer.current_trace_context().trace_id == s1.trace_id
            assert tracer.current_trace_context().span_id == s4.span_id

        assert tracer.current_span() == s1
        assert tracer.current_root_span() == s1

    assert tracer.current_span() is None
    assert tracer.current_root_span() is None
    assert s1.parent_id is None
    assert s2.parent_id == s1.span_id
    assert s3.parent_id == s2.span_id
    assert s4.parent_id == s1.span_id
    assert s1.trace_id == s2.trace_id == s3.trace_id == s4.trace_id
    assert s1.get_metric(_SAMPLING_PRIORITY_KEY) == 1
    assert s2.get_metric(_SAMPLING_PRIORITY_KEY) is None
    assert _ORIGIN_KEY not in s1.get_tags()

    t = test_spans.pop_traces()
    assert len(t) == 1
    assert len(t[0]) == 4
    _s1, _s2, _s3, _s4 = t[0]
    assert s1 == _s1
    assert s2 == _s2
    assert s3 == _s3
    assert s4 == _s4

    with tracer.trace("s") as s:
        assert s.parent_id is None
        assert s.trace_id != s1.trace_id


def test_multithreaded(tracer, test_spans):
    def target():
        with tracer.trace("s1"):
            with tracer.trace("s2"):
                pass
            with tracer.trace("s3"):
                pass

    for _ in range(1000):
        ts = [threading.Thread(target=target) for _ in range(10)]
        for t in ts:
            t.start()

        for t in ts:
            t.join()

        traces = test_spans.pop_traces()
        assert len(traces) == 10

        for trace in traces:
            assert len(trace) == 3


def test_ctx_distributed(tracer, test_spans):
    # Test activating an invalid context.
    ctx = Context(span_id=None, trace_id=None)
    tracer.context_provider.activate(ctx)
    assert tracer.current_span() is None

    with tracer.trace("test") as s1:
        assert tracer.current_span() == s1
        assert tracer.current_root_span() == s1
        assert tracer.current_trace_context().trace_id == s1.trace_id
        assert tracer.current_trace_context().span_id == s1.span_id
        assert s1.parent_id is None

    trace = test_spans.pop_traces()
    assert len(trace) == 1

    # Test activating a valid context.
    ctx = Context(span_id=1234, trace_id=4321, sampling_priority=2, dd_origin="somewhere")
    tracer.context_provider.activate(ctx)
    assert tracer.current_span() is None
    assert (
        tracer.current_trace_context()
        == tracer.context_provider.active()
        == Context(span_id=1234, trace_id=4321, sampling_priority=2, dd_origin="somewhere")
    )

    with tracer.trace("test2") as s2:
        assert tracer.current_span() == s2
        assert tracer.current_root_span() == s2
        assert tracer.current_trace_context().trace_id == s2.trace_id == 4321
        assert tracer.current_trace_context().span_id == s2.span_id
        assert s2.parent_id == 1234

    trace = test_spans.pop_traces()
    assert len(trace) == 1
    assert s2.get_metric(_SAMPLING_PRIORITY_KEY) == 2
    assert s2.get_tag(_ORIGIN_KEY) == "somewhere"


def test_manual_keep(tracer, test_spans):
    # On a root span
    with tracer.trace("asdf") as s:
        s.set_tag(MANUAL_KEEP_KEY)
    spans = test_spans.pop()
    assert spans[0].get_metric(_SAMPLING_PRIORITY_KEY) is USER_KEEP

    # On a child span
    with tracer.trace("asdf"):
        with tracer.trace("child") as s:
            s.set_tag(MANUAL_KEEP_KEY)
    spans = test_spans.pop()
    assert spans[0].get_metric(_SAMPLING_PRIORITY_KEY) is USER_KEEP


def test_manual_keep_then_drop(tracer, test_spans):
    # Test changing the value before finish.
    with tracer.trace("asdf") as root:
        with tracer.trace("child") as child:
            child.set_tag(MANUAL_KEEP_KEY)
        root.set_tag(MANUAL_DROP_KEY)
    spans = test_spans.pop()
    assert spans[0].get_metric(_SAMPLING_PRIORITY_KEY) is USER_REJECT


def test_manual_drop(tracer, test_spans):
    # On a root span
    with tracer.trace("asdf") as s:
        s.set_tag(MANUAL_DROP_KEY)
    spans = test_spans.pop()
    assert spans[0].get_metric(_SAMPLING_PRIORITY_KEY) is USER_REJECT

    # On a child span
    with tracer.trace("asdf"):
        with tracer.trace("child") as s:
            s.set_tag(MANUAL_DROP_KEY)
    spans = test_spans.pop()
    assert spans[0].get_metric(_SAMPLING_PRIORITY_KEY) is USER_REJECT


@mock.patch("ddtrace.internal.hostname.get_hostname")
def test_get_report_hostname_enabled(get_hostname, tracer, test_spans):
    get_hostname.return_value = "test-hostname"
    with override_global_config(dict(_report_hostname=True)):
        with tracer.trace("span"):
            with tracer.trace("child"):
                pass

    spans = test_spans.pop()
    root = spans[0]
    child = spans[1]
    assert root.get_tag(_HOSTNAME_KEY) == "test-hostname"
    assert child.get_tag(_HOSTNAME_KEY) is None


@mock.patch("ddtrace.internal.hostname.get_hostname")
def test_get_report_hostname_disabled(get_hostname, tracer, test_spans):
    get_hostname.return_value = "test-hostname"
    with override_global_config(dict(_report_hostname=False)):
        with tracer.trace("span"):
            with tracer.trace("child"):
                pass

    spans = test_spans.pop()
    root = spans[0]
    child = spans[1]
    assert root.get_tag(_HOSTNAME_KEY) is None
    assert child.get_tag(_HOSTNAME_KEY) is None


@mock.patch("ddtrace.internal.hostname.get_hostname")
def test_get_report_hostname_default(get_hostname, tracer, test_spans):
    get_hostname.return_value = "test-hostname"
    with override_global_config(dict(_report_hostname=False)):
        with tracer.trace("span"):
            with tracer.trace("child"):
                pass

    spans = test_spans.pop()
    root = spans[0]
    child = spans[1]
    assert root.get_tag(_HOSTNAME_KEY) is None
    assert child.get_tag(_HOSTNAME_KEY) is None


def test_non_active_span(tracer, test_spans):
    with tracer.start_span("test", activate=False):
        assert tracer.current_span() is None
        assert tracer.current_root_span() is None
    assert tracer.current_span() is None
    assert tracer.current_root_span() is None
    traces = test_spans.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1

    with tracer.start_span("test1", activate=False):
        with tracer.start_span("test2", activate=False):
            assert tracer.current_span() is None
            assert tracer.current_root_span() is None
    assert tracer.current_span() is None
    assert tracer.current_root_span() is None
    traces = test_spans.pop_traces()
    assert len(traces) == 2

    with tracer.start_span("active", activate=True) as active:
        with tracer.start_span("non active", child_of=active, activate=False):
            assert tracer.context_provider.active() is active
            assert tracer.current_root_span() is active
        assert tracer.context_provider.active() is active
        assert tracer.current_root_span() is active
    traces = test_spans.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 2


def test_service_mapping():
    @contextlib.contextmanager
    def override_service_mapping(service_mapping):
        with override_env(dict(DD_SERVICE_MAPPING=service_mapping)):
            assert ddtrace.config.service_mapping == {}
            ddtrace.config.service_mapping = Config().service_mapping
            yield
            ddtrace.config.service_mapping = {}

    # Test single mapping
    with override_service_mapping("foo:bar"), global_tracer.trace("renaming", service="foo") as span:
        assert span.service == "bar"

    # Test multiple mappings
    with override_service_mapping("foo:bar,sna:fu"), global_tracer.trace("renaming", service="sna") as span:
        assert span.service == "fu"

    # Test colliding mappings
    with override_service_mapping("foo:bar,foo:foobar"), global_tracer.trace("renaming", service="foo") as span:
        assert span.service == "foobar"

    # Test invalid service mapping
    with override_service_mapping("foo;bar,sna:fu"):
        with global_tracer.trace("passthru", service="foo") as _:
            assert _.service == "foo"
        with global_tracer.trace("renaming", "sna") as _:
            assert _.service == "fu"


@pytest.mark.subprocess(env={"DD_TRACE_AGENT_URL": "bad://localhost:1234"})
def test_bad_agent_url():
    import pytest

    with pytest.raises(ValueError) as e:
        from ddtrace.trace import tracer  # noqa: F401

    assert (
        str(e.value)
        == "Unsupported protocol 'bad' in intake URL 'bad://localhost:1234'. Must be one of: http, https, unix"
    )


@pytest.mark.subprocess(env={"DD_TRACE_AGENT_URL": "unix://"})
def test_bad_agent_url_invalid_path():
    import pytest

    with pytest.raises(ValueError) as e:
        from ddtrace.trace import tracer  # noqa: F401
    assert str(e.value) == "Invalid file path in intake URL 'unix://'"


@pytest.mark.subprocess(env={"DD_TRACE_AGENT_URL": "http://"})
def test_bad_agent_url_invalid_hostname():
    import pytest

    with pytest.raises(ValueError) as e:
        from ddtrace.trace import tracer  # noqa: F401
    assert str(e.value) == "Invalid hostname in intake URL 'http://'"


def test_context_priority(tracer, test_spans):
    """Assigning a sampling_priority should not affect if the trace is sent to the agent"""
    for p in [USER_REJECT, AUTO_REJECT, AUTO_KEEP, USER_KEEP, None, 999]:
        with tracer.trace("span_%s" % p) as span:
            span.context.sampling_priority = p

        # Spans should always be written regardless of sampling priority since
        # the agent needs to know the sampling decision.
        spans = test_spans.pop()
        assert len(spans) == 1, "trace should be sampled"
        if p in [USER_REJECT, AUTO_REJECT, AUTO_KEEP, USER_KEEP]:
            assert spans[0].get_metric(_SAMPLING_PRIORITY_KEY) == p


def test_spans_sampled_out(tracer, test_spans):
    with tracer.trace("root") as span:
        span.context.sampling_priority = 0
        with tracer.trace("child") as span:
            span.context.sampling_priority = 0
        with tracer.trace("child") as span:
            span.context.sampling_priority = 0

    spans = test_spans.pop()
    assert len(spans) == 3
    for span in spans:
        assert span.context.sampling_priority <= 0


def test_spans_sampled_one(tracer, test_spans):
    with tracer.trace("root") as span:
        span.context.sampling_priority = 0
        with tracer.trace("child") as span:
            span.context.sampling_priority = 0
        with tracer.trace("child") as span:
            span.context.sampling_priority = 1

    spans = test_spans.pop()
    assert len(spans) == 3


def test_spans_sampled_all(tracer, test_spans):
    with tracer.trace("root") as span:
        span.context.sampling_priority = 1
        with tracer.trace("child") as span:
            span.context.sampling_priority = 1
        with tracer.trace("child") as span:
            span.context.sampling_priority = 1

    spans = test_spans.pop()
    assert len(spans) == 3


def test_closing_other_context_spans_single_span(tracer, test_spans):
    """
    Ensure that a span created in one thread can be finished in another without
    breaking the active span management.
    """

    def _target(span):
        assert tracer.current_span() is None
        span.finish()
        assert tracer.current_span() is None

    span = tracer.trace("main thread")
    assert tracer.current_span() is span
    t1 = threading.Thread(target=_target, args=(span,))
    t1.start()
    t1.join(60)
    assert tracer.current_span() is None

    spans = test_spans.pop()
    assert len(spans) == 1


def test_closing_other_context_spans_multi_spans(tracer, test_spans):
    """
    Ensure that spans created in one thread can be finished in another without
    breaking the active span management.
    """

    def _target(span):
        assert tracer.current_span() is None
        span.finish()
        assert tracer.current_span() is None

    root = tracer.trace("root span")
    span = tracer.trace("child span")
    assert tracer.current_span() is span
    t1 = threading.Thread(target=_target, args=(span,))
    t1.start()
    t1.join(60)
    assert tracer.current_span() is root
    root.finish()

    spans = test_spans.pop()
    assert len(spans) == 2


@pytest.mark.subprocess(err=None)
def test_fork_manual_span_same_context():
    import ddtrace.auto  # noqa

    import os

    from ddtrace.trace import tracer

    span = tracer.trace("test")
    span.context.set_baggage_item("key", "value")
    span.context._meta["_dd.p.dm"] = "-1"
    pid = os.fork()

    if pid == 0:
        child = tracer.start_span("child", child_of=span)
        assert child.parent_id == span.span_id
        assert child._parent is None
        # Ensure the child context is the same as the parent context
        assert child.context == span.context
        # No more current span strong reference to avoid memory leaks.
        assert tracer.current_span() is None
        child.finish()
        os._exit(12)

    span.finish()
    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


@pytest.mark.subprocess(err=None)
def test_fork_manual_span_different_contexts():
    import ddtrace.auto  # noqa

    import os

    from ddtrace.trace import tracer

    span = tracer.start_span("test")
    pid = os.fork()

    if pid == 0:
        child = tracer.start_span("child", child_of=span)
        assert child.parent_id == span.span_id
        assert child._parent is None
        assert tracer.current_span() is None
        child.finish()
        os._exit(12)

    span.finish()
    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


@pytest.mark.subprocess(err=None)
def test_fork_pid():
    import ddtrace.auto  # noqa

    import os

    from ddtrace.constants import PID
    from ddtrace.trace import tracer

    root = tracer.trace("root_span")
    assert root.get_tag("runtime-id") is not None
    assert root.get_metric(PID) is not None

    pid = os.fork()

    if pid:
        child1 = tracer.trace("span1")
        assert child1.get_tag("runtime-id") is None
        assert child1.get_metric(PID) is None
        child1.finish()
    else:
        child2 = tracer.trace("span2")
        assert child2.get_tag("runtime-id") is not None
        assert child2.get_metric(PID) is not None
        child2.finish()
        os._exit(12)

    root.finish()
    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


@pytest.mark.subprocess
def test_tracer_api_version():
    from ddtrace.internal.encoding import MsgpackEncoderV05
    from ddtrace.trace import tracer as t

    assert isinstance(t._span_aggregator.writer._encoder, MsgpackEncoderV05)


@pytest.mark.subprocess(parametrize={"DD_TRACE_ENABLED": ["true", "false"]})
def test_tracer_memory_leak_span_processors():
    """
    Test whether the tracer or span processors will hold onto
    span references after the trace is complete.

    This is a regression test for the tracer not calling on_span_finish
    of SpanAggregator when the tracer was disabled and traces leaking.
    """
    import gc
    import weakref

    from ddtrace.trace import TraceFilter
    from ddtrace.trace import tracer as t

    spans = weakref.WeakSet()

    # Filter to ensure we don't send the traces to the writer
    class DropAllFilter(TraceFilter):
        def process_trace(self, trace):
            return None

    t.configure(trace_processors=[DropAllFilter()])

    for _ in range(5):
        with t.trace("test") as span:
            spans.add(span)

    # Be sure to dereference the last Span held by the local variable `span`
    span = None
    t.flush()

    # Force gc
    gc.collect()
    assert len(spans) == 0


def test_top_level(tracer):
    with tracer.trace("parent", service="my-svc") as parent_span:
        assert parent_span._is_top_level
        with tracer.trace("child") as child_span:
            assert not child_span._is_top_level
            with tracer.trace("subchild") as subchild_span:
                assert not subchild_span._is_top_level
            with tracer.trace("subchild2", service="svc-2") as subchild_span2:
                assert subchild_span2._is_top_level

    with tracer.trace("parent", service="my-svc") as parent_span:
        assert parent_span._is_top_level
        with tracer.trace("child", service="child-svc") as child_span:
            assert child_span._is_top_level
        with tracer.trace("child2", service="child-svc") as child_span2:
            assert child_span2._is_top_level


def test_finish_span_with_ancestors(tracer):
    # single span case
    span1 = tracer.trace("span1")
    span1.finish_with_ancestors()
    assert span1.finished

    # multi ancestor case
    span1 = tracer.trace("span1")
    span2 = tracer.trace("span2")
    span3 = tracer.trace("span2")
    span3.finish_with_ancestors()
    assert span1.finished
    assert span2.finished
    assert span3.finished


def test_ctx_api(tracer):
    from ddtrace.internal import core

    assert core.get_item("key") is None

    with tracer.trace("root") as span:
        v = core.get_item("my.val")
        assert v is None

        core.set_item("appsec.key", "val", span=span)
        core.set_items({"appsec.key2": "val2", "appsec.key3": "val3"}, span=span)
        assert core.get_item("appsec.key", span=span) == "val"
        assert core.get_item("appsec.key2", span=span) == "val2"
        assert core.get_item("appsec.key3", span=span) == "val3"
        assert core.get_items(["appsec.key"], span=span) == ["val"]
        assert core.get_items(["appsec.key", "appsec.key2", "appsec.key3"], span=span) == ["val", "val2", "val3"]

        with tracer.trace("child") as childspan:
            assert core.get_item("appsec.key", span=childspan) == "val"

    assert core.get_item("appsec.key") is None
    assert core.get_items(["appsec.key"]) == [None]


@pytest.mark.parametrize("sca_enabled", ["true", "false"])
@pytest.mark.parametrize("appsec_enabled", [True, False])
@pytest.mark.parametrize("iast_enabled", [True, False])
def test_asm_standalone_configuration(sca_enabled, appsec_enabled, iast_enabled):
    if not appsec_enabled and not iast_enabled and sca_enabled == "false":
        pytest.skip("SCA, AppSec or IAST must be enabled")

    with override_env({"DD_APPSEC_SCA_ENABLED": sca_enabled}):
        ddtrace.config._reset()
        tracer = DummyTracer()
        tracer.configure(appsec_enabled=appsec_enabled, iast_enabled=iast_enabled, apm_tracing_disabled=True)
        if sca_enabled == "true":
            assert bool(ddtrace.config._sca_enabled) is True
        assert tracer.enabled is False

        assert isinstance(tracer._sampler.limiter, RateLimiter)
        assert tracer._sampler.limiter.rate_limit == 1
        assert tracer._sampler.limiter.time_window == 60e9

        assert tracer._span_aggregator.sampling_processor._compute_stats_enabled is False

    # reset tracer values
    with override_env({"DD_APPSEC_SCA_ENABLED": "false"}):
        ddtrace.config._reset()
        tracer.configure(appsec_enabled=False, iast_enabled=False, apm_tracing_disabled=False)


def test_gc_not_used_on_root_spans():
    gc.freeze()

    with ddtrace.tracer.trace("test-event"):
        pass

    # There should be no more span objects lingering around.
    assert not any(str(obj).startswith("<Span") for obj in gc.get_objects())

    # To check the exact nature of the objects and their references, use the following:

    # for i, obj in enumerate(objects):
    #     print("--------------------")
    #     print(f"object {i}:", obj)
    #     print("referrers:", [f"object {objects.index(r)}" for r in gc.get_referrers(obj)[:-2]])
    #     print("referents:", [f"object {objects.index(r)}" if r in objects else r for r in gc.get_referents(obj)])
    #     print("--------------------")


@pytest.mark.subprocess(env=dict(AWS_LAMBDA_FUNCTION_NAME="my-func"))
def test_detect_agent_config_with_lambda_extension():
    import mock

    def mock_os_path_exists(path):
        return path == "/opt/extensions/datadog-agent"

    with mock.patch("os.path.exists", side_effect=mock_os_path_exists):
        import ddtrace
        from ddtrace.internal.writer import AgentWriter
        from ddtrace.trace import tracer

        assert ddtrace.internal.serverless.in_aws_lambda()

        assert ddtrace.internal.serverless.has_aws_lambda_agent_extension()

        assert isinstance(tracer._span_aggregator.writer, AgentWriter)
        assert tracer._span_aggregator.writer._sync_mode


@pytest.mark.subprocess()
def test_multiple_tracer_instances():
    import mock

    import ddtrace

    assert ddtrace.trace.tracer is not None
    with mock.patch("ddtrace._trace.tracer.log") as log:
        ddtrace.trace.Tracer()
    log.error.assert_called_once_with(
        "Initializing multiple Tracer instances is not supported. Use ``ddtrace.trace.tracer`` instead.",
    )


def test_span_creation_with_context(tracer):
    """Test that a span created with a Context parent correctly sets its parent context.

    This test verifies that when a span is created with a Context object as its parent,
    the span's _parent_context attribute is properly set to that Context.
    """

    context = Context(trace_id=123, span_id=321)
    with tracer.start_span("s1", child_of=context) as span:
        assert span._parent_context == context


def test_activate_context_propagates_to_child_spans(tracer):
    """Test that activating a context properly propagates to child spans.

    This test verifies that when a context is activated using _activate_context:
    1. Child spans created within the context inherit the trace_id and parent_id
    2. Multiple child spans within the same context maintain consistent parentage
    3. Spans created outside the context have different trace_id and parent_id
    """
    with tracer._activate_context(Context(trace_id=1, span_id=1)):
        with tracer.trace("child1") as c1:
            assert c1.trace_id == 1
            assert c1.parent_id == 1

        with tracer.trace("child1") as c2:
            assert c2.trace_id == 1
            assert c2.parent_id == 1

    with tracer.trace("root") as root:
        assert root.trace_id != 1
        assert root.parent_id != 1


def test_activate_context_nesting_and_restoration(tracer):
    """Test that context activation properly handles nesting and restoration.

    This test verifies that:
    1. A context can be activated and its values are accessible
    2. A nested context can be activated and its values override the outer context
    3. When the nested context exits, the outer context is properly restored
    4. When all contexts exit, the active context is None
    """

    with tracer._activate_context(Context(trace_id=1, span_id=1)):
        active = tracer.context_provider.active()
        assert active.trace_id == 1
        assert active.span_id == 1

        with tracer._activate_context(Context(trace_id=2, span_id=2)):
            active = tracer.context_provider.active()
            assert active.trace_id == 2
            assert active.span_id == 2

        active = tracer.context_provider.active()
        assert active.trace_id == 1
        assert active.span_id == 1

    assert tracer.context_provider.active() is None
