import unittest

raise unittest.SkipTest("skipping tests for now. not real yet")

from nose.tools import eq_, ok_
from nose.plugins.attrib import attr
import gevent
import gevent.local
import thread
import threading


class GeventGlobalScopeTest(unittest.TestCase):
    def setUp(self):
        # simulate standard app bootstrap
        from gevent import monkey; monkey.patch_thread()
        from ddtrace import tracer

    def test_global_patch(self):
        from ddtrace import tracer; tracer.enabled = False

        # Ensure the patch is active
        ok_(isinstance(tracer.span_buffer._locals, gevent.local.local))

        seen_resources = []
        def worker_function(parent):
            tracer.span_buffer.set(parent)
            seen_resources.append(tracer.span_buffer.get().resource)

            with tracer.trace("greenlet.call") as span:
                span.resource = "sibling"

                gevent.sleep()

                # Ensure we have the correct parent span even after a context switch
                eq_(tracer.span_buffer.get().span_id, span.span_id)
                with tracer.trace("greenlet.other_call") as child:
                    child.resource = "sibling_child"

        with tracer.trace("web.request") as span:
            span.service = "web"
            span.resource = "parent"
            worker_count = 5
            workers = [gevent.spawn(worker_function, span) for w in range(worker_count)]
            gevent.joinall(workers)

        # Ensure all greenlets see the right parent span
        ok_("sibling" not in seen_resources)
        ok_(all(s == "parent" for s in seen_resources))

    def tearDown(self):
        # undo gevent monkey patching
        reload(thread); reload(threading)
        from ddtrace.buffer import ThreadLocalSpanBuffer
        from ddtrace import tracer; tracer.span_buffer = ThreadLocalSpanBuffer()


class GeventLocalScopeTest(unittest.TestCase):

    def test_unpatched(self):
        """
        Demonstrate a situation where thread-local storage leads to a bad tree:
            1. Main thread spawns several coroutines
            2. A coroutine is handed context from a sibling coroutine
            3. A coroutine incorrectly sees a "sibling" span as its parent
        """
        from ddtrace import tracer; tracer.enabled = False

        seen_resources = []
        def my_worker_function(i):
            ok_(tracer.span_buffer.get())
            seen_resources.append(tracer.span_buffer.get().resource)

            with tracer.trace("greenlet.call") as span:
                span.resource = "sibling"
                gevent.sleep()

        with tracer.trace("web.request") as span:
            span.service = "web"
            span.resource = "parent"

            worker_count = 5
            workers = [gevent.spawn(my_worker_function, w) for w in range(worker_count)]
            gevent.joinall(workers)

        # check that a bad parent span was seen
        ok_("sibling" in seen_resources)

    def test_local_patch(self):
        """
        Test patching a parent span into a coroutine's tracer
        """
        from ddtrace import tracer; tracer.enabled = False
        from ddtrace.contrib.gevent import GreenletLocalSpanBuffer

        def fn(parent):
            tracer.span_buffer = GreenletLocalSpanBuffer()
            tracer.span_buffer.set(parent)

            with tracer.trace("greenlet.call") as span:
                span.service = "greenlet"

                gevent.sleep()

                # Ensure we have the correct parent span even after a context switch
                eq_(tracer.span_buffer.get().span_id, span.span_id)
                with tracer.trace("greenlet.child_call") as child:
                    eq_(child.parent_id, span.span_id)

        with tracer.trace("web.request") as span:
            span.service = "web"
            worker = gevent.spawn(fn, span)
            worker.join()

    def tearDown(self):
        # undo gevent monkey patching
        reload(thread); reload(threading)
        from ddtrace.buffer import ThreadLocalSpanBuffer
        from ddtrace import tracer; tracer.span_buffer = ThreadLocalSpanBuffer()
