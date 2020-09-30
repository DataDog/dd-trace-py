import txredisapi
import twisted
from twisted.internet import defer
from twisted.internet import reactor

from ddtrace.contrib.twisted import patch as twisted_patch, unpatch as twisted_unpatch
from ddtrace.contrib.txredisapi import patch, unpatch

from tests import TracerTestCase, snapshot
from tests.contrib.config import REDIS_CONFIG


class TestTXRedisAPI(TracerTestCase):
    """
    Since twisted is required to run these tests we need to use
    subprocess test cases so that we can use the reactor.

    See the twisted test cases in test_twisted.py for more information.
    """

    def setUp(self):
        twisted_patch()
        patch()
        super(TestTXRedisAPI, self).setUp()

    def tearDown(self):
        twisted_unpatch()
        unpatch()
        super(TestTXRedisAPI, self).tearDown()

    @snapshot()
    def test_operations(self):
        results = []

        @defer.inlineCallbacks
        def main():
            rc = yield txredisapi.Connection(port=REDIS_CONFIG["port"])

            yield rc.set("foo", "bar")
            v = yield rc.get("foo")
            results.append(v)
            yield rc.disconnect()

        quit = lambda _: reactor.stop()
        d = main().addCallbacks(quit, quit)
        reactor.run()
        assert not isinstance(d.result, twisted.python.failure.Failure)
        assert results == ["bar"]
