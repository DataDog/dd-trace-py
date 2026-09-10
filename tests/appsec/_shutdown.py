"""Bounded tracer shutdown for the test servers' /shutdown endpoints.

The endpoints exist to flush traces before the server is killed. They run inside a request, so
whatever they do has to finish well before appsec_utils gives up on the response after 10s.

Two things make that hard under a gevent worker. tracer.shutdown() blocks the calling thread on a
native condvar in PeriodicThread_join, and under gevent that thread is the hub -- measured: 0
greenlets scheduled for the whole call, so the response cannot be written even after the view
returns. And the bound is only as good as the timeout that is passed; an untimed shutdown parks
forever, and a shutdown timed at the caller's own 10s is a coin flip.

So: run it off the hub, and cap it. Overrunning the cap loses some traces, which is strictly
better than losing the response.
"""

import sys
import time


DEFAULT_TIMEOUT = 5.0


def _log(message: str) -> None:
    """Report to the server's stderr, which is the output appsec_utils points a failure at.

    The response body carries the same status, but a failure here is precisely the case where the
    response never arrives, so the log is the only copy that survives. The wall clock is included
    so these lines can be lined up against gunicorn's own timestamped output.
    """
    print("[shutdown %s] %s" % (time.strftime("%H:%M:%S"), message), file=sys.stderr, flush=True)


def start_hub_watchdog(threshold: float = 1.0, poll: float = 0.1) -> None:
    """Report gevent event-loop stalls, so a silent gap in the log can be attributed.

    When the /shutdown response is late there are three candidates: the endpoint was slow, the
    event loop stopped running so nothing could be read or written, or the delay was outside the
    server entirely. The endpoint already times itself, and this covers the second -- otherwise a
    frozen loop and a slow client look identical from the log.

    A blocked loop cannot run this greenlet either, which is the point: the gap it measures on
    resuming is the length of the freeze. Silent unless something actually stalls.
    """
    if not _gevent_patched():
        return

    import gevent

    def _watch() -> None:
        last = time.monotonic()
        while True:
            gevent.sleep(poll)
            now = time.monotonic()
            stalled = now - last - poll
            if stalled >= threshold:
                _log("event loop stalled for %.3fs" % stalled)
            last = now

    gevent.spawn(_watch)


def _gevent_patched():
    try:
        from gevent import monkey
    except ImportError:
        return False
    return monkey.is_module_patched("threading")


def bounded_tracer_shutdown(timeout: float = DEFAULT_TIMEOUT) -> str:
    """Shut the tracer down in at most `timeout` seconds. Returns a short status for the response.

    The status is echoed into the endpoint's body so a CI failure shows how long this took and
    whether it hit the cap, instead of leaving a silent gap in the server log.
    """
    from ddtrace.trace import tracer

    started = time.monotonic()
    _log("entered, timeout=%.1fs gevent=%s" % (timeout, _gevent_patched()))

    if not _gevent_patched():
        tracer.shutdown(timeout=timeout)
        status = "direct"
    else:
        import gevent

        worker = gevent.get_hub().threadpool.spawn(tracer.shutdown, timeout=timeout)
        try:
            worker.get(timeout=timeout + 1.0)
            status = "offloaded"
        except gevent.Timeout:
            status = "offloaded, CAPPED (traces dropped)"
        except Exception as exc:
            status = "offloaded, failed: %r" % (exc,)

    result = "shutdown %s in %.3fs" % (status, time.monotonic() - started)
    _log(result)
    return result
