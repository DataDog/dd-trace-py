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
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_greenlet_count",
        DD_PROFILING_UPLOAD_INTERVAL="1",
    ),
    err=None,
)
def test_greenlet_count_present():
    """greenlet_count is present and positive when gevent greenlets are active."""
    from gevent import monkey

    monkey.patch_all()

    import glob
    import json
    import os
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

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    files = sorted(glob.glob(output_filename + ".*.internal_metadata.json"))
    assert files, "Expected at least one internal_metadata.json file"

    found_positive = False
    for f in files:
        with open(f) as fp:
            metadata = json.load(fp)
        if "greenlet_count" in metadata:
            assert isinstance(metadata["greenlet_count"], int)
            assert metadata["greenlet_count"] >= 0
            if metadata["greenlet_count"] > 0:
                found_positive = True

    assert found_positive, "Expected at least one metadata file with greenlet_count > 0"
