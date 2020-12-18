import twisted
from twisted.enterprise import adbapi
from twisted.internet import reactor, task

from ddtrace import Pin
from ddtrace.contrib.twisted import patch, unpatch
from ddtrace.contrib.mysqldb import patch as mysql_patch

from tests import TracerTestCase, snapshot
from ..config import MYSQL_CONFIG


class TestTwisted(TracerTestCase):
    """
    All test cases are written as subprocess test cases because we can't
    reuse the reactor between test cases and it mimics real programs
    better than twisted's test utils.
    """

    def setUp(self):
        super(TestTwisted, self).setUp()
        patch()
        mysql_patch()
        pin = Pin.get_from(twisted)
        self.original_tracer = pin.tracer
        Pin.override(twisted, tracer=self.tracer)

    def tearDown(self):
        Pin.override(twisted, tracer=self.original_tracer)
        unpatch()
        super(TestTwisted, self).tearDown()

    @TracerTestCase.run_in_subprocess
    def test_propagation_1(self):
        def cb():
            self.tracer.trace("s2").finish()
            reactor.stop()

        s1 = self.tracer.trace("s1")

        task.deferLater(reactor, 0, cb)
        reactor.run()
        s1.finish()

        spans = self.get_spans()
        assert len(spans) == 2
        s1 = list(self.filter_spans(name="s1"))[0]
        s2 = list(self.filter_spans(name="s2"))[0]

        assert s1.parent_id is None
        assert s1.trace_id == s2.trace_id
        assert s2.parent_id == s1.span_id

    @TracerTestCase.run_in_subprocess
    def test_propagation_2(self):
        def cb1():
            self.tracer.trace("s2").finish()

        def cb2():
            self.tracer.trace("s3").finish()
            reactor.stop()

        with self.tracer.trace("s1"):
            task.deferLater(reactor, 0, cb1)
            task.deferLater(reactor, 0, cb2)
            reactor.run()

        spans = self.get_spans()
        assert len(spans) == 3
        s1 = list(self.filter_spans(name="s1"))[0]
        s2 = list(self.filter_spans(name="s2"))[0]
        s3 = list(self.filter_spans(name="s3"))[0]

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
            reactor.stop()

        task.deferLater(reactor, 0, cb1)
        task.deferLater(reactor, 0, cb2)
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

        def cb1(s):
            self.tracer.trace("cb1").finish()
            return s

        def cb2(s):
            self.tracer.trace("cb2").finish()
            reactor.stop()
            return s

        d = task.deferLater(reactor, 0, fn).addCallback(cb1).addCallback(cb2)
        reactor.run()
        s = d.result
        s.finish()

        spans = self.tracer.writer.pop()
        assert len(spans) == 3

        s1, s2, s3 = spans
        # traces should have separate contexts
        assert s1.trace_id != s2.trace_id
        assert s2.trace_id != s3.trace_id
        assert s1.trace_id != s3.trace_id

    @TracerTestCase.run_in_subprocess
    def test_propagation_2_callbacks_separate_traces(self):
        def fn():
            self.tracer.trace("s").finish()

        def cb(_):
            self.tracer.trace("cb").finish()

        task.deferLater(reactor, 0, fn).addCallback(cb).addCallback(cb)
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

        task.deferLater(reactor, 0, fn1)
        task.deferLater(reactor, 0, fn2)
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

        d = task.deferLater(reactor, 0, fn1)

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


class TestTwistedSnapshot(TracerTestCase):
    def setUp(self):
        super(TestTwistedSnapshot, self).setUp()
        patch()
        mysql_patch()

    def tearDown(self):
        unpatch()
        super(TestTwistedSnapshot, self).tearDown()

    @TracerTestCase.run_in_subprocess
    @snapshot()
    def test_connectionpool(self):
        dbpool = adbapi.ConnectionPool("MySQLdb", **MYSQL_CONFIG)

        def cb(data):
            try:
                assert data == ((1,),)
            finally:
                reactor.stop()

        d = dbpool.runQuery("SELECT 1").addCallback(cb)
        reactor.run()
        assert not isinstance(d.result, twisted.python.failure.Failure)
