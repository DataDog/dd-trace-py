"""ddtest-specific conftest helpers.

Encapsulates the _DD_PYTEST_XDIST_INFERRED_SERVICE handling so that
removing ddtest support is a simple matter of deleting this file and
reverting the import + call site in tests/conftest.py.
"""

import os
import sys


def pop_and_seed_inferred_service():
    """Pop _DD_PYTEST_XDIST_INFERRED_SERVICE and seed the detect_service cache.

    Consumed by detect_service() during ddtrace import; unset now so
    it doesn't leak into tests (e.g. unit tests that call detect_service directly).

    Save it so pytest_configure can propagate the correct value to xdist workers
    (the suitespec may set this to the suite-level service, e.g. tests.tracer;
    detect_service(sys.argv) under ddtest would pick the first file's subpackage).

    Seed the detect_service cache so subsequent Config() calls in tests return
    the same service as import time. Without this, detect_service(sys.argv) in
    a test would re-compute from sys.argv (which under ddtest has individual
    files, yielding a subpackage like tests.tracer.runtime instead of
    tests.tracer). Under riot CI xdist workers (sys.argv=['-c']), the natural
    result is None, so we only seed when the env var differs from the natural
    result — this avoids breaking tests that expect config.service is None.
    """
    _inferred_service_env = os.environ.pop("_DD_PYTEST_XDIST_INFERRED_SERVICE", None)
    if _inferred_service_env:
        from ddtrace.internal.settings._inferred_base_service import CACHE as _detect_service_cache
        from ddtrace.internal.settings._inferred_base_service import detect_service as _detect_service

        _natural = _detect_service(sys.argv)
        if _natural != _inferred_service_env:
            _detect_service_cache[tuple(sorted(sys.argv))] = _inferred_service_env
    return _inferred_service_env
