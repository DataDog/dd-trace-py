"""
tests for Tracer and utilities.
"""
import contextlib
import multiprocessing
from os import getpid
import warnings

from unittest.case import SkipTest

import mock
import pytest

import ddtrace
from ddtrace.ext import system
from ddtrace.context import Context
from ddtrace.constants import VERSION_KEY, ENV_KEY

from tests.subprocesstest import run_in_subprocess
from tests.base import BaseTracerTestCase
from tests.utils.tracer import DummyTracer
from tests.utils.tracer import DummyWriter  # noqa
from ddtrace.internal.writer import LogWriter, AgentWriter


def get_dummy_tracer():
    return DummyTracer()


class TracerTestCase(BaseTracerTestCase):
    def test_tracer_vars(self):
        span = self.trace('a', service='s', resource='r', span_type='t')
        span.assert_matches(name='a', service='s', resource='r', span_type='t')
        # DEV: Finish to ensure we don't leak `service` between spans
        span.finish()

        span = self.trace('a')
        span.assert_matches(name='a', service=None, resource='a', span_type=None)

    def test_tracer(self):
        def _mix():
            with self.trace('cake.mix'):
                pass

        def _bake():
            with self.trace('cake.bake'):
                pass

        def _make_cake():
            with self.trace('cake.make') as span:
                span.service = 'baker'
                span.resource = 'cake'
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
            dict(name='cake.make', resource='cake', service='baker', parent_id=None),
            (
                # Span with no children
                dict(name='cake.mix', resource='cake.mix', service='baker'),
                # Span with no children
                dict(name='cake.bake', resource='cake.bake', service='baker'),
            ),
        )

        # do it again and make sure it has new trace ids
        self.reset()
        _make_cake()
        self.assert_span_count(3)
        for s in self.spans:
            assert s.trace_id != root_trace_id

    def test_tracer_wrap(self):
        @self.tracer.wrap('decorated_function', service='s', resource='r', span_type='t')
        def f(tag_name, tag_value):
            # make sure we can still set tags
            span = self.tracer.current_span()
            span.set_tag(tag_name, tag_value)

        f('a', 'b')

        self.assert_span_count(1)
        span = self.get_root_span()
        span.assert_matches(
            name='decorated_function', service='s', resource='r', span_type='t', meta=dict(a='b'),
        )

    def test_tracer_pid(self):
        with self.trace('root') as root_span:
            with self.trace('child') as child_span:
                pass

        # Root span should contain the pid of the current process
        root_span.assert_metrics({system.PID: getpid()}, exact=False)

        # Child span should not contain a pid tag
        child_span.assert_metrics(dict(), exact=True)

    def test_tracer_wrap_default_name(self):
        @self.tracer.wrap()
        def f():
            pass

        f()

        self.assert_structure(dict(name='tests.tracer.test_tracer.f'))

    def test_tracer_wrap_exception(self):
        @self.tracer.wrap()
        def f():
            raise Exception('bim')

        with self.assertRaises(Exception) as ex:
            f()

            self.assert_structure(
                dict(
                    name='tests.test_tracer.f',
                    error=1,
                    meta={
                        'error.msg': ex.message,
                        'error.type': ex.__class__.__name__,
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
        @self.tracer.wrap('inner')
        def inner():
            root_span = self.tracer.current_root_span()
            self.assertEqual(root_span.name, 'outer')

        @self.tracer.wrap('outer')
        def outer():
            root_span = self.tracer.current_root_span()
            self.assertEqual(root_span.name, 'outer')

            with self.trace('mid'):
                root_span = self.tracer.current_root_span()
                self.assertEqual(root_span.name, 'outer')

                inner()

        outer()

    def test_tracer_wrap_span_nesting(self):
        @self.tracer.wrap('inner')
        def inner():
            pass

        @self.tracer.wrap('outer')
        def outer():
            with self.trace('mid'):
                inner()

        outer()

        self.assert_span_count(3)
        self.assert_structure(
            dict(name='outer'),
            (
                (
                    dict(name='mid'),
                    (
                        dict(name='inner'),
                    )
                ),
            ),
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
        self.spans[0].assert_matches(name='tests.tracer.test_tracer.s')
        self.spans[1].assert_matches(name='tests.tracer.test_tracer.c')
        self.spans[2].assert_matches(name='tests.tracer.test_tracer.i')

    def test_tracer_wrap_factory(self):
        def wrap_executor(tracer, fn, args, kwargs, span_name=None, service=None, resource=None, span_type=None):
            with tracer.trace('wrap.overwrite') as span:
                span.set_tag('args', args)
                span.set_tag('kwargs', kwargs)
                return fn(*args, **kwargs)

        @self.tracer.wrap()
        def wrapped_function(param, kw_param=None):
            self.assertEqual(42, param)
            self.assertEqual(42, kw_param)

        # set the custom wrap factory after the wrapper has been called
        self.tracer.configure(wrap_executor=wrap_executor)

        # call the function expecting that the custom tracing wrapper is used
        wrapped_function(42, kw_param=42)

        self.assert_span_count(1)
        self.spans[0].assert_matches(
            name='wrap.overwrite',
            meta=dict(args='(42,)', kwargs='{\'kw_param\': 42}'),
        )

    def test_tracer_wrap_factory_nested(self):
        def wrap_executor(tracer, fn, args, kwargs, span_name=None, service=None, resource=None, span_type=None):
            with tracer.trace('wrap.overwrite') as span:
                span.set_tag('args', args)
                span.set_tag('kwargs', kwargs)
                return fn(*args, **kwargs)

        @self.tracer.wrap()
        def wrapped_function(param, kw_param=None):
            self.assertEqual(42, param)
            self.assertEqual(42, kw_param)

        # set the custom wrap factory after the wrapper has been called
        self.tracer.configure(wrap_executor=wrap_executor)

        # call the function expecting that the custom tracing wrapper is used
        with self.trace('wrap.parent', service='webserver'):
            wrapped_function(42, kw_param=42)

        self.assert_structure(
            dict(name='wrap.parent', service='webserver'),
            (
                dict(
                    name='wrap.overwrite',
                    service='webserver',
                    meta=dict(args='(42,)', kwargs='{\'kw_param\': 42}')
                ),
            ),
        )

    def test_tracer_disabled(self):
        self.tracer.enabled = True
        with self.trace('foo') as s:
            s.set_tag('a', 'b')

        self.assert_has_spans()
        self.reset()

        self.tracer.enabled = False
        with self.trace('foo') as s:
            s.set_tag('a', 'b')
        self.assert_has_no_spans()

    def test_unserializable_span_with_finish(self):
        try:
            import numpy as np
        except ImportError:
            raise SkipTest('numpy not installed')

        # a weird case where manually calling finish with an unserializable
        # span was causing an loop of serialization.
        with self.trace('parent') as span:
            span.metrics['as'] = np.int64(1)  # circumvent the data checks
            span.finish()

    def test_tracer_disabled_mem_leak(self):
        # ensure that if the tracer is disabled, we still remove things from the
        # span buffer upon finishing.
        self.tracer.enabled = False
        s1 = self.trace('foo')
        s1.finish()

        p1 = self.tracer.current_span()
        s2 = self.trace('bar')

        self.assertIsNone(s2._parent)
        s2.finish()
        self.assertIsNone(p1)

    def test_tracer_global_tags(self):
        s1 = self.trace('brie')
        s1.finish()
        self.assertIsNone(s1.get_tag('env'))
        self.assertIsNone(s1.get_tag('other'))

        self.tracer.set_tags({'env': 'prod'})
        s2 = self.trace('camembert')
        s2.finish()
        self.assertEqual(s2.get_tag('env'), 'prod')
        self.assertIsNone(s2.get_tag('other'))

        self.tracer.set_tags({'env': 'staging', 'other': 'tag'})
        s3 = self.trace('gruyere')
        s3.finish()
        self.assertEqual(s3.get_tag('env'), 'staging')
        self.assertEqual(s3.get_tag('other'), 'tag')

    def test_global_context(self):
        # the tracer uses a global thread-local Context
        span = self.trace('fake_span')
        ctx = self.tracer.get_call_context()
        self.assertEqual(len(ctx._trace), 1)
        self.assertEqual(ctx._trace[0], span)

    def test_tracer_current_span(self):
        # the current span is in the local Context()
        span = self.trace('fake_span')
        self.assertEqual(self.tracer.current_span(), span)

    def test_tracer_current_span_missing_context(self):
        self.assertIsNone(self.tracer.current_span())

    def test_tracer_current_root_span_missing_context(self):
        self.assertIsNone(self.tracer.current_root_span())

    def test_default_provider_get(self):
        # Tracer Context Provider must return a Context object
        # even if empty
        ctx = self.tracer.context_provider.active()
        self.assertTrue(isinstance(ctx, Context))
        self.assertEqual(len(ctx._trace), 0)

    def test_default_provider_set(self):
        # The Context Provider can set the current active Context;
        # this could happen in distributed tracing
        ctx = Context(trace_id=42, span_id=100)
        self.tracer.context_provider.activate(ctx)
        span = self.trace('web.request')
        span.assert_matches(name='web.request', trace_id=42, parent_id=100)

    def test_default_provider_trace(self):
        # Context handled by a default provider must be used
        # when creating a trace
        span = self.trace('web.request')
        ctx = self.tracer.context_provider.active()
        self.assertEqual(len(ctx._trace), 1)
        self.assertEqual(span._context, ctx)

    def test_start_span(self):
        # it should create a root Span
        span = self.start_span('web.request')
        span.assert_matches(
            name='web.request',
            tracer=self.tracer,
            _parent=None,
            parent_id=None,
        )
        self.assertIsNotNone(span._context)
        self.assertEqual(span._context._current_span, span)

    def test_start_span_optional(self):
        # it should create a root Span with arguments
        span = self.start_span('web.request', service='web', resource='/', span_type='http')
        span.assert_matches(
            name='web.request',
            service='web',
            resource='/',
            span_type='http',
        )

    def test_start_span_service_default(self):
        span = self.start_span("")
        span.assert_matches(
            service=None
        )

    def test_start_span_service_from_parent(self):
        with self.start_span("parent", service="mysvc") as parent:
            child = self.start_span("child", child_of=parent)

        child.assert_matches(
            name="child",
            service="mysvc",
        )

    def test_start_span_service_global_config(self):
        # When no service is provided a default
        with self.override_global_config(dict(service="mysvc")):
            span = self.start_span("")
            span.assert_matches(
                service="mysvc"
            )

    def test_start_span_service_global_config_parent(self):
        # Parent should have precedence over global config
        with self.override_global_config(dict(service="mysvc")):
            with self.start_span("parent", service="parentsvc") as parent:
                child = self.start_span("child", child_of=parent)

        child.assert_matches(
            name="child",
            service="parentsvc",
        )

    def test_start_child_span(self):
        # it should create a child Span for the given parent
        parent = self.start_span('web.request')
        child = self.start_span('web.worker', child_of=parent)

        parent.assert_matches(
            name='web.request',
            parent_id=None,
            _context=child._context,
            _parent=None,
            tracer=self.tracer,
        )
        child.assert_matches(
            name='web.worker',
            parent_id=parent.span_id,
            _context=parent._context,
            _parent=parent,
            tracer=self.tracer,
        )

        self.assertEqual(child._context._current_span, child)

    def test_start_child_span_attributes(self):
        # it should create a child Span with parent's attributes
        parent = self.start_span('web.request', service='web', resource='/', span_type='http')
        child = self.start_span('web.worker', child_of=parent)
        child.assert_matches(name='web.worker', service='web')

    def test_start_child_from_context(self):
        # it should create a child span with a populated Context
        root = self.start_span('web.request')
        context = root.context
        child = self.start_span('web.worker', child_of=context)

        child.assert_matches(
            name='web.worker',
            parent_id=root.span_id,
            trace_id=root.trace_id,
            _context=root._context,
            _parent=root,
            tracer=self.tracer,
        )
        self.assertEqual(child._context._current_span, child)

    def test_adding_services(self):
        self.assertEqual(self.tracer._services, set())
        root = self.start_span('root', service='one')
        context = root.context
        self.assertSetEqual(self.tracer._services, set(['one']))
        self.start_span('child', service='two', child_of=context)
        self.assertSetEqual(self.tracer._services, set(['one', 'two']))

    def test_configure_runtime_worker(self):
        # by default runtime worker not started though runtime id is set
        self.assertIsNone(self.tracer._runtime_worker)

        # configure tracer with runtime metrics collection
        self.tracer.configure(collect_metrics=True)
        self.assertIsNotNone(self.tracer._runtime_worker)

    def test_configure_dogstatsd_host(self):
        with warnings.catch_warnings(record=True) as ws:
            warnings.simplefilter('always')
            self.tracer.configure(dogstatsd_host='foo')
            assert self.tracer._dogstatsd_client.host == 'foo'
            assert self.tracer._dogstatsd_client.port == 8125
            # verify warnings triggered
            assert len(ws) >= 1
            for w in ws:
                if issubclass(w.category, ddtrace.utils.deprecation.RemovedInDDTrace10Warning):
                    assert 'Use `dogstatsd_url`' in str(w.message)
                    break
            else:
                assert 0, "dogstatsd warning not found"

    def test_configure_dogstatsd_host_port(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            self.tracer.configure(dogstatsd_host='foo', dogstatsd_port='1234')
            assert self.tracer._dogstatsd_client.host == 'foo'
            assert self.tracer._dogstatsd_client.port == 1234
            # verify warnings triggered
            assert len(w) >= 2
            assert issubclass(w[0].category, ddtrace.utils.deprecation.RemovedInDDTrace10Warning)
            assert 'Use `dogstatsd_url`' in str(w[0].message)
            assert issubclass(w[1].category, ddtrace.utils.deprecation.RemovedInDDTrace10Warning)
            assert 'Use `dogstatsd_url`' in str(w[1].message)

    def test_configure_dogstatsd_url_host_port(self):
        self.tracer.configure(dogstatsd_url='foo:1234')
        assert self.tracer._dogstatsd_client.host == 'foo'
        assert self.tracer._dogstatsd_client.port == 1234

    def test_configure_dogstatsd_url_socket(self):
        self.tracer.configure(dogstatsd_url='unix:///foo.sock')
        assert self.tracer._dogstatsd_client.host is None
        assert self.tracer._dogstatsd_client.port is None
        assert self.tracer._dogstatsd_client.socket_path == '/foo.sock'

    def test_span_no_runtime_tags(self):
        self.tracer.configure(collect_metrics=False)

        root = self.start_span('root')
        context = root.context
        child = self.start_span('child', child_of=context)

        self.assertIsNone(root.get_tag('language'))

        self.assertIsNone(child.get_tag('language'))

    def test_only_root_span_runtime_internal_span_types(self):
        self.tracer.configure(collect_metrics=True)

        for span_type in ("custom", "template", "web", "worker"):
            root = self.start_span('root', span_type=span_type)
            context = root.context
            child = self.start_span('child', child_of=context)

            self.assertEqual(root.get_tag('language'), 'python')

            self.assertIsNone(child.get_tag('language'))

    def test_only_root_span_runtime_external_span_types(self):
        self.tracer.configure(collect_metrics=True)

        for span_type in ("algoliasearch.search", "boto", "cache", "cassandra", "elasticsearch",
                          "grpc", "kombu", "http", "memcached", "redis", "sql", "vertica"):
            root = self.start_span('root', span_type=span_type)
            context = root.context
            child = self.start_span('child', child_of=context)

            self.assertIsNone(root.get_tag('language'))

            self.assertIsNone(child.get_tag('language'))


def test_tracer_url():
    t = ddtrace.Tracer()
    assert t.writer.api.hostname == 'localhost'
    assert t.writer.api.port == 8126

    t = ddtrace.Tracer(url='http://foobar:12')
    assert t.writer.api.hostname == 'foobar'
    assert t.writer.api.port == 12

    t = ddtrace.Tracer(url='unix:///foobar')
    assert t.writer.api.uds_path == '/foobar'

    t = ddtrace.Tracer(url='http://localhost')
    assert t.writer.api.hostname == 'localhost'
    assert t.writer.api.port == 80
    assert not t.writer.api.https

    t = ddtrace.Tracer(url='https://localhost')
    assert t.writer.api.hostname == 'localhost'
    assert t.writer.api.port == 443
    assert t.writer.api.https

    with pytest.raises(ValueError) as e:
        t = ddtrace.Tracer(url='foo://foobar:12')
        assert str(e) == 'Unknown scheme `https` for agent URL'


def test_tracer_shutdown_no_timeout():
    t = ddtrace.Tracer()
    t.writer = mock.Mock(wraps=t.writer)

    # The writer thread does not start until the first write.
    t.shutdown()
    assert not t.writer.stop.called
    assert not t.writer.join.called

    # Do a write to start the writer.
    with t.trace("something"):
        pass
    t.shutdown()
    t.writer.stop.assert_called_once_with()
    t.writer.join.assert_called_once_with(timeout=None)


def test_tracer_configure_writer_stop_unstarted():
    t = ddtrace.Tracer()
    t.writer = mock.Mock(wraps=t.writer)
    orig_writer = t.writer

    # Make sure we aren't calling stop for an unstarted writer
    t.configure(hostname="localhost", port=8126)
    assert not orig_writer.stop.called


def test_tracer_configure_writer_stop_started():
    t = ddtrace.Tracer()
    t.writer = mock.Mock(wraps=t.writer)
    orig_writer = t.writer

    # Do a write to start the writer
    with t.trace("something"):
        pass

    t.configure(hostname="localhost", port=8126)
    orig_writer.stop.assert_called_once_with()


def test_tracer_shutdown_timeout():
    t = ddtrace.Tracer()
    t.writer = mock.Mock(wraps=t.writer)

    with t.trace("something"):
        pass

    t.shutdown(timeout=2)
    t.writer.stop.assert_called_once_with()
    t.writer.join.assert_called_once_with(timeout=2)


def test_tracer_dogstatsd_url():
    t = ddtrace.Tracer()
    assert t._dogstatsd_client.host == 'localhost'
    assert t._dogstatsd_client.port == 8125

    t = ddtrace.Tracer(dogstatsd_url='foobar:12')
    assert t._dogstatsd_client.host == 'foobar'
    assert t._dogstatsd_client.port == 12

    t = ddtrace.Tracer(dogstatsd_url='udp://foobar:12')
    assert t._dogstatsd_client.host == 'foobar'
    assert t._dogstatsd_client.port == 12

    t = ddtrace.Tracer(dogstatsd_url='/var/run/statsd.sock')
    assert t._dogstatsd_client.socket_path == '/var/run/statsd.sock'

    t = ddtrace.Tracer(dogstatsd_url='unix:///var/run/statsd.sock')
    assert t._dogstatsd_client.socket_path == '/var/run/statsd.sock'

    with pytest.raises(ValueError) as e:
        t = ddtrace.Tracer(dogstatsd_url='foo://foobar:12')
        assert str(e) == 'Unknown url format for `foo://foobar:12`'


def test_tracer_fork():
    t = ddtrace.Tracer()
    original_pid = t._pid
    original_writer = t.writer

    @contextlib.contextmanager
    def capture_failures(errors):
        try:
            yield
        except AssertionError as e:
            errors.put(e)

    def task(t, errors):
        # Start a new span to trigger process checking
        with t.trace('test', service='test') as span:

            # Assert we recreated the writer and have a new queue
            with capture_failures(errors):
                assert t._pid != original_pid
                assert t.writer != original_writer
                assert t.writer._trace_queue != original_writer._trace_queue

        # Assert the trace got written into the correct queue
        assert len(original_writer._trace_queue) == 0
        assert len(t.writer._trace_queue) == 1
        assert [[span]] == list(t.writer._trace_queue.get())

    # Assert tracer in a new process correctly recreates the writer
    errors = multiprocessing.Queue()
    p = multiprocessing.Process(target=task, args=(t, errors))
    try:
        p.start()
    finally:
        p.join(timeout=2)

    assert errors.empty(), errors.get()

    # Ensure writing into the tracer in this process still works as expected
    with t.trace('test', service='test') as span:
        assert t._pid == original_pid
        assert t.writer == original_writer
        assert t.writer._trace_queue == original_writer._trace_queue

    # Assert the trace got written into the correct queue
    assert len(original_writer._trace_queue) == 1
    assert len(t.writer._trace_queue) == 1
    assert [[span]] == list(t.writer._trace_queue.get())


def test_tracer_trace_across_fork():
    """
    When a trace is started in a parent process and a child process is spawned
        The trace should be continued in the child process
    """
    tracer = ddtrace.Tracer()
    tracer.writer = DummyWriter()

    def task(tracer, q):
        tracer.writer = DummyWriter()
        with tracer.trace("child"):
            pass
        spans = tracer.writer.pop()
        q.put([dict(trace_id=s.trace_id, parent_id=s.parent_id) for s in spans])

    # Assert tracer in a new process correctly recreates the writer
    q = multiprocessing.Queue()
    with tracer.trace("parent") as parent:
        p = multiprocessing.Process(target=task, args=(tracer, q))
        p.start()
        p.join()

    children = q.get()
    assert len(children) == 1
    child, = children
    assert parent.trace_id == child["trace_id"]
    assert child["parent_id"] == parent.span_id


def test_tracer_trace_across_multiple_forks():
    """
    When a trace is started and crosses multiple process boundaries
        The trace should be continued in the child processes
    """
    tracer = ddtrace.Tracer()
    tracer.writer = DummyWriter()

    # Start a span in this process then start a child process which itself
    # starts a span and spawns another child process which starts a span.
    def task(tracer, q):
        tracer.writer = DummyWriter()

        def task2(tracer, q):
            tracer.writer = DummyWriter()

            with tracer.trace("child2"):
                pass

            spans = tracer.writer.pop()
            q.put([dict(trace_id=s.trace_id, parent_id=s.parent_id) for s in spans])

        with tracer.trace("child1"):
            q2 = multiprocessing.Queue()
            p = multiprocessing.Process(target=task2, args=(tracer, q2))
            p.start()
            p.join()

        task2_spans = q2.get()
        spans = tracer.writer.pop()
        q.put([
            dict(trace_id=s.trace_id, parent_id=s.parent_id, span_id=s.span_id)
            for s in spans
        ] + task2_spans)

    # Assert tracer in a new process correctly recreates the writer
    q = multiprocessing.Queue()
    with tracer.trace("parent") as parent:
        p = multiprocessing.Process(target=task, args=(tracer, q))
        p.start()
        p.join()

    children = q.get()
    assert len(children) == 2
    child1, child2 = children
    assert parent.trace_id == child1["trace_id"] == child2["trace_id"]
    assert child1["parent_id"] == parent.span_id
    assert child2["parent_id"] == child1["span_id"]


def test_tracer_with_version():
    t = ddtrace.Tracer()

    # With global `config.version` defined
    with BaseTracerTestCase.override_global_config(dict(version='1.2.3')):
        with t.trace('test.span') as span:
            assert span.get_tag(VERSION_KEY) == '1.2.3'

            # override manually
            span.set_tag(VERSION_KEY, '4.5.6')
            assert span.get_tag(VERSION_KEY) == '4.5.6'

    # With no `config.version` defined
    with t.trace('test.span') as span:
        assert span.get_tag(VERSION_KEY) is None

        # explicitly set in the span
        span.set_tag(VERSION_KEY, '1.2.3')
        assert span.get_tag(VERSION_KEY) == '1.2.3'

    # With global tags set
    t.set_tags({VERSION_KEY: 'tags.version'})
    with BaseTracerTestCase.override_global_config(dict(version='config.version')):
        with t.trace('test.span') as span:
            assert span.get_tag(VERSION_KEY) == 'config.version'


def test_tracer_with_env():
    t = ddtrace.Tracer()

    # With global `config.env` defined
    with BaseTracerTestCase.override_global_config(dict(env='prod')):
        with t.trace('test.span') as span:
            assert span.get_tag(ENV_KEY) == 'prod'

            # override manually
            span.set_tag(ENV_KEY, 'prod-staging')
            assert span.get_tag(ENV_KEY) == 'prod-staging'

    # With no `config.env` defined
    with t.trace('test.span') as span:
        assert span.get_tag(ENV_KEY) is None

        # explicitly set in the span
        span.set_tag(ENV_KEY, 'prod-staging')
        assert span.get_tag(ENV_KEY) == 'prod-staging'

    # With global tags set
    t.set_tags({ENV_KEY: 'tags.env'})
    with BaseTracerTestCase.override_global_config(dict(env='config.env')):
        with t.trace('test.span') as span:
            assert span.get_tag(ENV_KEY) == 'config.env'


class EnvTracerTestCase(BaseTracerTestCase):
    """Tracer test cases requiring environment variables.
    """
    @run_in_subprocess(env_overrides=dict(DATADOG_SERVICE_NAME="mysvc"))
    def test_service_name_legacy_DATADOG_SERVICE_NAME(self):
        """
        When DATADOG_SERVICE_NAME is provided
            It should not be used by default
            It should be used with config._get_service()
        """
        from ddtrace import config
        assert config.service is None
        with self.start_span("") as s:
            s.assert_matches(service=None)
        with self.start_span("", service=config._get_service()) as s:
            s.assert_matches(service="mysvc")

    @run_in_subprocess(env_overrides=dict(DD_SERVICE_NAME="mysvc"))
    def test_service_name_legacy_DD_SERVICE_NAME(self):
        """
        When DD_SERVICE_NAME is provided
            It should not be used by default
            It should be used with config._get_service()
        """
        from ddtrace import config
        assert config.service is None
        with self.start_span("") as s:
            s.assert_matches(service=None)
        with self.start_span("", service=config._get_service()) as s:
            s.assert_matches(service="mysvc")

    @run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_service_name_env(self):
        span = self.start_span("")
        span.assert_matches(
            service="mysvc",
        )

    @run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_service_name_env_global_config(self):
        # Global config should have higher precedence than the environment variable
        with self.override_global_config(dict(service="overridesvc")):
            span = self.start_span("")
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
            assert VERSION_KEY not in root.meta
            with self.trace("child") as span:
                assert VERSION_KEY not in span.meta

    @run_in_subprocess(env_overrides=dict(DD_SERVICE="django", DD_VERSION="0.1.2"))
    def test_version_service(self):
        # Fleshed out example of service and version tagging

        # Our app is called django, we provide DD_SERVICE=django and DD_VERSION=0.1.2

        with self.trace("django.request") as root:
            # Root span should be tagged
            assert root.service == "django"
            assert VERSION_KEY in root.meta and root.meta[VERSION_KEY] == "0.1.2"

            # Child spans should be tagged
            with self.trace("") as child1:
                assert child1.service == "django"
                assert VERSION_KEY in child1.meta and child1.meta[VERSION_KEY] == "0.1.2"

            # Version should not be applied to spans of a service that isn't user-defined
            with self.trace("mysql.query", service="mysql") as span:
                assert VERSION_KEY not in span.meta
                # Child should also not have a version
                with self.trace("") as child2:
                    assert child2.service == "mysql"
                    assert VERSION_KEY not in child2.meta

    @run_in_subprocess(env_overrides=dict(AWS_LAMBDA_FUNCTION_NAME="my-func"))
    def test_detect_agentless_env(self):
        assert isinstance(self.tracer.original_writer, LogWriter)

    @run_in_subprocess(env_overrides=dict(AWS_LAMBDA_FUNCTION_NAME="my-func", DD_AGENT_HOST="localhost"))
    def test_detect_agent_config(self):
        assert isinstance(self.tracer.original_writer, AgentWriter)

    @run_in_subprocess(env_overrides=dict(DD_TAGS="key1:value1,key2:value2"))
    def test_dd_tags(self):
        assert self.tracer.tags["key1"] == "value1"
        assert self.tracer.tags["key2"] == "value2"

    @run_in_subprocess(env_overrides=dict(DD_TAGS="key1:value1,key2:value2,key3"))
    def test_dd_tags_invalid(self):
        assert "key1" in self.tracer.tags
        assert "key2" in self.tracer.tags
        assert "key3" not in self.tracer.tags

    @run_in_subprocess(env_overrides=dict(DD_TAGS="service:mysvc,env:myenv,version:myvers"))
    def test_tags_from_DD_TAGS(self):
        t = ddtrace.Tracer()
        with t.trace("test") as s:
            assert s.service == "mysvc"
            assert s.get_tag("env") == "myenv"
            assert s.get_tag("version") == "myvers"

    @run_in_subprocess(env_overrides=dict(
        DD_TAGS="service:s,env:e,version:v",
        DD_ENV="env",
        DD_SERVICE="svc",
        DD_VERSION="0.123",
    ))
    def test_tags_from_DD_TAGS_precedence(self):
        t = ddtrace.Tracer()
        with t.trace("test") as s:
            assert s.service == "svc"
            assert s.get_tag("env") == "env"
            assert s.get_tag("version") == "0.123"

    @run_in_subprocess(env_overrides=dict(DD_TAGS="service:mysvc,env:myenv,version:myvers"))
    def test_tags_from_DD_TAGS_override(self):
        t = ddtrace.Tracer()
        ddtrace.config.env = "env"
        ddtrace.config.service = "service"
        ddtrace.config.version = "0.123"
        with t.trace("test") as s:
            assert s.service == "service"
            assert s.get_tag("env") == "env"
            assert s.get_tag("version") == "0.123"


def test_tracer_custom_max_traces(monkeypatch):
    monkeypatch.setenv("DD_TRACE_MAX_TPS", "2000")
    tracer = ddtrace.Tracer()
    assert tracer.writer._trace_queue.maxsize == 2000


def test_tracer_set_runtime_tags():
    t = ddtrace.Tracer()
    span = t.start_span("foobar")

    assert len(span.get_tag("runtime-id"))

    t2 = ddtrace.Tracer()
    span2 = t2.start_span("foobaz")

    assert span.get_tag("runtime-id") == span2.get_tag("runtime-id")


def test_tracer_runtime_tags_fork():
    tracer = ddtrace.Tracer()

    def task(tracer, q):
        span = tracer.start_span("foobaz")
        q.put(span.get_tag("runtime-id"))

    span = tracer.start_span("foobar")

    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=task, args=(tracer, q))
    p.start()
    p.join()

    children_tag = q.get()
    assert children_tag != span.get_tag("runtime-id")


def test_start_span_hooks():
    t = ddtrace.Tracer()

    result = {}

    @t.on_start_span
    def store_span(span):
        result['span'] = span

    span = t.start_span("hello")

    assert span == result["span"]


def test_deregister_start_span_hooks():
    t = ddtrace.Tracer()

    result = {}

    @t.on_start_span
    def store_span(span):
        result['span'] = span

    t.deregister_on_start_span(store_span)

    t.start_span("hello")

    assert result == {}


def test_enable(monkeypatch):
    t1 = ddtrace.Tracer()
    assert t1.enabled

    monkeypatch.setenv("DD_TRACE_ENABLED", "false")
    t2 = ddtrace.Tracer()
    assert not t2.enabled
