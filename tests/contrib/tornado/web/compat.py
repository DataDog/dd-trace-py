from tornado.concurrent import Future
from tornado.ioloop import IOLoop


def sleep(duration):
    """
    Compatibility helper that return a Future() that can be yielded.
    This is used because Tornado 4.0 doesn't have a ``gen.sleep()``
    function, that we require to test the ``TracerStackContext``.
    """
    f = Future()
    IOLoop.current().call_later(duration, lambda: f.set_result(None))
    return f
