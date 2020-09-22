from twisted.internet import reactor, defer, task

from ddtrace.contrib.twisted import patch, unpatch

from tests import TracerTestCase


class TestTwisted(TracerTestCase):
    def setUp(self):
        super(TestTwisted, self).setUp()
        patch()

    def tearDown(self):
        super(TestTwisted, self).tearDown()
        unpatch()

    def test_propagation_inline(self):
        def cb():
            self.tracer.trace("s2").finish()

        s1 = self.tracer.trace("s1")

        def close_s1(s):
            s.finish()

        task.deferLater(reactor, 0.001, cb).addCallback(close_s1, s1)
        reactor.callLater(0.01, reactor.stop)
        reactor.run()
        s1.finish()

        spans = self.tracer.writer.pop()
        assert len(spans) == 2
        s1, s2 = spans

        assert s1.parent_id is None
        assert s1.trace_id == s2.trace_id
        assert s2.parent_id == s1.span_id

    @defer.inlineCallbacks
    def test_inline(self):
        pass
