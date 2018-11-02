"""
tests for Tracer and utilities.
"""

from os import getpid

from unittest.case import SkipTest

from ddtrace.ext import system
from ddtrace.context import Context

from . import BaseTracerTestCase
from .utils.tracer import DummyTracer
from .utils.tracer import DummyWriter  # noqa


def get_dummy_tracer():
    return DummyTracer()


class TracerTestCase(BaseTracerTestCase):
    def test_tracer_vars(self):
        span = self.tracer.trace('a', service='s', resource='r', span_type='t')
        span.assert_matches(name='a', service='s', resource='r', span_type='t')
        # DEV: Finish to ensure we don't leak `service` between spans
        span.finish()

        span = self.tracer.trace('a')
        span.assert_matches(name='a', service=None, resource='a', span_type=None)

    def test_tracer(self):
        def _mix():
            with self.tracer.trace('cake.mix'):
                pass

        def _bake():
            with self.tracer.trace('cake.bake'):
                pass

        def _make_cake():
            with self.tracer.trace('cake.make') as span:
                span.service = 'baker'
                span.resource = 'cake'
                _mix()
                _bake()

        # let's run it and make sure all is well.
        self.assert_has_no_spans()
        _make_cake()

        root = self.get_root_span()
        self.assert_structure(
            # Root span with 2 children
            dict(name='cake.make', resource='cake', service='baker', parent_id=None),
            (
                (
                    # Span with no children
                    dict(name='cake.mix', resource='cake.mix', service='baker'), (),
                ),
                (
                    # Span with no children
                    dict(name='cake.bake', resource='cake.bake', service='baker'), (),
                ),
            ),
        )

        # do it again and make sure it has new trace ids
        self.reset()
        _make_cake()
        self.assert_span_count(3)
        for s in self.spans:
            assert s.trace_id != root.trace_id

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
        with self.tracer.trace('root') as root_span:
            with self.tracer.trace('child') as child_span:
                pass

        # Root span should contain the pid of the current process
        root_span.assert_meta({system.PID: str(getpid())}, exact=True)

        # Child span should not contain a pid tag
        child_span.assert_meta(dict(), exact=True)

    def test_tracer_wrap_default_name(self):
        @self.tracer.wrap()
        def f():
            pass

        f()

        self.assert_structure(dict(name='tests.test_tracer.f'), ())

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
                (),
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

            with self.tracer.trace('mid'):
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
            with self.tracer.trace('mid'):
                inner()

        outer()

        self.assert_span_count(3)
        self.assert_structure(
            dict(name='outer'),
            (
                (
                    dict(name='mid'),
                    (
                        (dict(name='inner'), ()),
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
        self.spans[0].assert_matches(name='tests.test_tracer.s')
        self.spans[1].assert_matches(name='tests.test_tracer.c')
        self.spans[2].assert_matches(name='tests.test_tracer.i')

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
        with self.tracer.trace('wrap.parent', service='webserver'):
            wrapped_function(42, kw_param=42)

        self.assert_structure(
            dict(name='wrap.parent', service='webserver'),
            (
                (
                    dict(
                        name='wrap.overwrite',
                        service='webserver',
                        meta=dict(args='(42,)', kwargs='{\'kw_param\': 42}')
                    ),
                    (),
                ),
            ),
        )

    def test_tracer_disabled(self):
        self.tracer.enabled = True
        with self.tracer.trace('foo') as s:
            s.set_tag('a', 'b')

        self.assert_has_spans()
        self.reset()

        self.tracer.enabled = False
        with self.tracer.trace('foo') as s:
            s.set_tag('a', 'b')
        self.assert_has_no_spans()

    def test_unserializable_span_with_finish(self):
        try:
            import numpy as np
        except ImportError:
            raise SkipTest('numpy not installed')

        # a weird case where manually calling finish with an unserializable
        # span was causing an loop of serialization.
        with self.tracer.trace('parent') as span:
            span.metrics['as'] = np.int64(1) # circumvent the data checks
            span.finish()

    def test_tracer_disabled_mem_leak(self):
        # ensure that if the tracer is disabled, we still remove things from the
        # span buffer upon finishing.
        self.tracer.enabled = False
        s1 = self.tracer.trace('foo')
        s1.finish()

        p1 = self.tracer.current_span()
        s2 = self.tracer.trace('bar')

        self.assertIsNone(s2._parent)
        s2.finish()
        self.assertIsNone(p1)

    def test_tracer_global_tags(self):
        s1 = self.tracer.trace('brie')
        s1.finish()
        self.assertIsNone(s1.get_tag('env'))
        self.assertIsNone(s1.get_tag('other'))

        self.tracer.set_tags({'env': 'prod'})
        s2 = self.tracer.trace('camembert')
        s2.finish()
        self.assertEqual(s2.get_tag('env'), 'prod')
        self.assertIsNone(s2.get_tag('other'))

        self.tracer.set_tags({'env': 'staging', 'other': 'tag'})
        s3 = self.tracer.trace('gruyere')
        s3.finish()
        self.assertEqual(s3.get_tag('env'), 'staging')
        self.assertEqual(s3.get_tag('other'), 'tag')

    def test_global_context(self):
        # the tracer uses a global thread-local Context
        span = self.tracer.trace('fake_span')
        ctx = self.tracer.get_call_context()
        self.assertEqual(len(ctx._trace), 1)
        self.assertEqual(ctx._trace[0], span)

    def test_tracer_current_span(self):
        # the current span is in the local Context()
        span = self.tracer.trace('fake_span')
        self.assertEqual(self.tracer.current_span(), span)

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
        span = self.tracer.trace('web.request')
        span.assert_matches(name='web.request', trace_id=42, parent_id=100)

    def test_default_provider_trace(self):
        # Context handled by a default provider must be used
        # when creating a trace
        span = self.tracer.trace('web.request')
        ctx = self.tracer.context_provider.active()
        self.assertEqual(len(ctx._trace), 1)
        self.assertEqual(span._context, ctx)

    def test_start_span(self):
        # it should create a root Span
        span = self.tracer.start_span('web.request')
        span.assert_matches(
            name='web.request',
            _tracer=self.tracer,
            _parent=None,
            parent_id=None,
        )
        self.assertIsNotNone(span._context)
        self.assertEqual(span._context._current_span, span)

    def test_start_span_optional(self):
        # it should create a root Span with arguments
        span = self.tracer.start_span('web.request', service='web', resource='/', span_type='http')
        span.assert_matches(
            name='web.request',
            service='web',
            resource='/',
            span_type='http',
        )

    def test_start_child_span(self):
        # it should create a child Span for the given parent
        parent = self.tracer.start_span('web.request')
        child = self.tracer.start_span('web.worker', child_of=parent)

        parent.assert_matches(
            name='web.request',
            parent_id=None,
            _context=child._context,
            _parent=None,
            _tracer=self.tracer,
        )
        child.assert_matches(
            name='web.worker',
            parent_id=parent.span_id,
            _context=parent._context,
            _parent=parent,
            _tracer=self.tracer,
        )

        self.assertEqual(child._context._current_span, child)

    def test_start_child_span_attributes(self):
        # it should create a child Span with parent's attributes
        parent = self.tracer.start_span('web.request', service='web', resource='/', span_type='http')
        child = self.tracer.start_span('web.worker', child_of=parent)
        child.assert_matches(name='web.worker', service='web')

    def test_start_child_from_context(self):
        # it should create a child span with a populated Context
        root = self.tracer.start_span('web.request')
        context = root.context
        child = self.tracer.start_span('web.worker', child_of=context)

        child.assert_matches(
            name='web.worker',
            parent_id=root.span_id,
            trace_id=root.trace_id,
            _context=root._context,
            _parent=root,
            _tracer=self.tracer,
        )
        self.assertEqual(child._context._current_span, child)
