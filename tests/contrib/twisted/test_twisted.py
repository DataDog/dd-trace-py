import twisted
from twisted.internet import reactor, task

from ddtrace.contrib.twisted import patch, unpatch

from tests import TracerTestCase


class TestTwisted(TracerTestCase):
    """
    All test cases are written as subprocess test cases because we can't
    reuse the reactor between test cases and it mimics real programs
    better than twisted's test utils.
    """

    def setUp(self):
        super(TestTwisted, self).setUp()
        patch()

    def tearDown(self):
        super(TestTwisted, self).tearDown()
        unpatch()

    @TracerTestCase.run_in_subprocess
    def test_propagation_1(self):
        def cb():
            self.tracer.trace("s2").finish()

        s1 = self.tracer.trace("s1")

        task.deferLater(reactor, 0.001, cb)
        reactor.callLater(0.01, reactor.stop)
        reactor.run()
        s1.finish()

        spans = self.tracer.writer.pop()
        assert len(spans) == 2
        s1, s2 = spans

        assert s1.parent_id is None
        assert s1.trace_id == s2.trace_id
        assert s2.parent_id == s1.span_id

    @TracerTestCase.run_in_subprocess
    def test_propagation_2(self):
        def cb1():
            self.tracer.trace("s2").finish()

        def cb2():
            self.tracer.trace("s3").finish()

        with self.tracer.trace("s1"):
            task.deferLater(reactor, 0.001, cb1)
            task.deferLater(reactor, 0.001, cb2)
            reactor.callLater(0.01, reactor.stop)
            reactor.run()

        spans = self.tracer.writer.pop()
        assert len(spans) == 3
        s1, s2, s3 = spans

        assert s1.parent_id is None
        assert s1.trace_id == s2.trace_id == s3.trace_id
        assert s2.parent_id == s1.span_id
        assert s3.parent_id == s1.span_id

    @TracerTestCase.run_in_subprocess
    def test_propagation_3(self):
        def cb1():
            self.tracer.trace("s1").finish()

        def cb2():
            self.tracer.trace("s2").finish()

        task.deferLater(reactor, 0.001, cb1)
        task.deferLater(reactor, 0.001, cb2)
        reactor.callLater(0.01, reactor.stop)
        reactor.run()

        spans = self.tracer.writer.pop()
        assert len(spans) == 2
        s1, s2 = spans

        assert s1.trace_id != s2.trace_id
        assert s1.parent_id is None
        assert s2.parent_id is None

    @TracerTestCase.run_in_subprocess
    def test_propagation_2_callbacks_continue_trace(self):
        def fn():
            s = self.tracer.trace("s")
            return s

        def cb(s):
            self.tracer.trace("cb").finish()
            return s

        d = task.deferLater(reactor, 0.001, fn).addCallback(cb).addCallback(cb)
        reactor.callLater(0.01, reactor.stop)
        reactor.run()
        s = d.result
        s.finish()

        spans = self.tracer.writer.pop()
        assert len(spans) == 3

        s1, s2, s3 = spans
        assert s1.trace_id == s2.trace_id == s3.trace_id

    @TracerTestCase.run_in_subprocess
    def test_propagation_2_callbacks_separate_traces(self):
        def fn():
            self.tracer.trace("s").finish()

        def cb(_):
            self.tracer.trace("cb").finish()

        task.deferLater(reactor, 0.001, fn).addCallback(cb).addCallback(cb)
        reactor.callLater(0.01, reactor.stop)
        reactor.run()

        spans = self.tracer.writer.pop()
        assert len(spans) == 3

        s1, s2, s3 = spans
        assert s1.trace_id != s2.trace_id
        assert s2.trace_id != s3.trace_id
        assert s1.trace_id != s3.trace_id

    @TracerTestCase.run_in_subprocess
    def test_propagation_2_deferreds(self):
        """
        Deferred1 -> close_span
        Deferred2 -> close_span

        Should produce
        trace 1:
        [    s1     ]

        trace 2:
        [    s2     ]
        """

        def close_span(s):
            s.finish()

        def fn1():
            s = self.tracer.trace("s1")
            return task.deferLater(reactor, 0.005, close_span, s)

        def fn2():
            s = self.tracer.trace("s2")
            return task.deferLater(reactor, 0.005, close_span, s)

        task.deferLater(reactor, 0.001, fn1)
        task.deferLater(reactor, 0.001, fn2)
        reactor.callLater(0.01, reactor.stop)
        reactor.run()

        spans = self.tracer.writer.pop()
        assert len(spans) == 2

        s1, s2 = spans
        assert s1.trace_id != s2.trace_id
        assert s1.parent_id is None
        assert s2.parent_id is None

    @TracerTestCase.run_in_subprocess
    def test_callback_double_activate(self):
        def fn1():
            return 3

        d = task.deferLater(reactor, 0.001, fn1)

        def fn2(args):
            def fn():
                return

            # The context will be activated by the patching code as well.
            ctx = getattr(d, "__ctx")
            ctx.run(fn)
            return 1

        d.addCallback(fn2)

        reactor.callLater(0.01, reactor.stop)
        reactor.run()
        assert d.result == 1

    @TracerTestCase.run_in_subprocess
    def test_callback_runtime_error(self):
        def fn1():
            return 3

        d = task.deferLater(reactor, 0.001, fn1)

        def fn2(_):
            raise RuntimeError

        d.addCallback(fn2)

        reactor.callLater(0.01, reactor.stop)
        reactor.run()
        assert isinstance(d.result, twisted.python.failure.Failure)
