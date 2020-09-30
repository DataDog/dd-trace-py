import txredisapi
import twisted
from twisted.internet import defer
from twisted.internet import reactor

from tests import TracerTestCase


class TestTXRedisAPI(TracerTestCase):
    """
    Since twisted is required to run these tests we need to use
    subprocess test cases so that we can use the reactor.

    See the twisted test cases in test_twisted.py for more information.
    """

    def test_request(self):
        @defer.inlineCallbacks
        def main():
            rc = yield txredisapi.Connection()

            yield rc.set("foo", "bar")
            v = yield rc.get("foo")
            assert v == "bar"
            yield rc.disconnect()

        d = main().addCallback(lambda ign: reactor.stop())
        reactor.run()
        assert not isinstance(d.result, twisted.python.failure.Failure)
