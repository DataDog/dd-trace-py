import os
import sys

import pytest


GEVENT_COMPATIBLE_WITH_PYTHON_VERSION = os.getenv("DD_PROFILE_TEST_GEVENT", False) and (
    sys.version_info < (3, 11, 9) or sys.version_info >= (3, 12, 5)
)


@pytest.mark.skipif(
    not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION,
    reason="gevent is not available or not compatible with this Python version",
)
@pytest.mark.subprocess(err=None)
def test_greenlet_count_present():
    """greenlet_count is present and positive when gevent greenlets are active."""
    # Start the mini agent BEFORE monkey.patch_all() so that its server socket is
    # created with standard Python sockets. After gevent patches Python sockets,
    # a pre-existing server socket continues to work correctly with gevent's
    # event loop.  We also can't call server.stop() after monkey.patch_all()
    # because socketserver.shutdown() deadlocks with gevent-patched threading.
    # The server runs on a daemon thread and will be killed when the subprocess exits.
    import os
    import uuid

    from ddtrace.internal.datadog.profiling import ddup
    from tests.profiling.utils import _ProfilingMiniAgent
    from tests.profiling.utils import _ProfilingMiniAgentClient
    from tests.profiling.utils import get_all_metadata_from_agent

    _token = str(uuid.uuid4())
    _server = _ProfilingMiniAgent()
    _server.start()
    _base_url = f"http://127.0.0.1:{_server.port}"

    os.environ["_DD_PROFILING_TEST_TOKEN"] = _token
    os.environ["_DD_PROFILING_TEST_PROXY_URL"] = _base_url
    _config_test_token = getattr(ddup, "config_test_token", None)
    if _config_test_token is not None:
        _config_test_token(_token, _base_url)

    # Force 1s upload interval so we get multiple uploads during the 3s sleep.
    os.environ["DD_PROFILING_UPLOAD_INTERVAL"] = "1"

    from gevent import monkey

    monkey.patch_all()

    import time

    import gevent

    from ddtrace.profiling import profiler
    from ddtrace.trace import tracer

    stop = False

    def worker():
        while not stop:
            gevent.sleep(0.01)

    p = profiler.Profiler(tracer=tracer)
    p.start()

    greenlets = [gevent.spawn(worker) for _ in range(10)]
    time.sleep(3)

    stop = True
    gevent.joinall(greenlets, timeout=5)
    p.stop()

    _client = _ProfilingMiniAgentClient(base_url=_base_url, token=_token)
    files = get_all_metadata_from_agent(_client, min_count=2)
    assert files, "Expected at least one metadata upload"

    found_positive = False
    for metadata in files:
        if "greenlet_count" in metadata:
            assert isinstance(metadata["greenlet_count"], int)
            assert metadata["greenlet_count"] >= 0
            if metadata["greenlet_count"] > 0:
                found_positive = True

    assert found_positive, "Expected at least one metadata file with greenlet_count > 0"

    # Don't call _server.stop() here: after monkey.patch_all(), calling
    # socketserver.shutdown() deadlocks because threading.Event.wait() is gevent-patched
    # and tries to switch to the gevent hub which no longer exists at this point.
    # The server runs on a daemon thread and will be killed when the subprocess exits.
