# Description: Test the crashtracker module

import sys

import pytest


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_loading():
    try:
        pass
    except Exception:
        pytest.fail("Crashtracker failed to load")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_available():
    import ddtrace.internal.core.crashtracker as crashtracker

    assert crashtracker.is_available


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_config():
    import pytest
    import ddtrace.internal.core.crashtracker as crashtracker

    try:
        crashtracker.set_url("http://localhost:1234")
        crashtracker.set_service("my_favorite_service")
        crashtracker.set_version("v0.0.0.0.0.0.1")
        crashtracker.set_runtime("4kph")
        crashtracker.set_runtime_version("v3.1.4.1")
        crashtracker.set_library_version("v2.7.1.8")
        crashtracker.set_stdout_filename("stdout.log")
        crashtracker.set_stderr_filename("stderr.log")
        crashtracker.set_alt_stack(False)
        crashtracker.set_resolve_frames_full()
        assert crashtracker.start()
    except Exception:
        pytest.fail("Exception when starting crashtracker")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_config_bytes():
    import pytest
    import ddtrace.internal.core.crashtracker as crashtracker

    try:
        crashtracker.set_url(b"http://localhost:1234")
        crashtracker.set_service(b"my_favorite_service")
        crashtracker.set_version(b"v0.0.0.0.0.0.1")
        crashtracker.set_runtime(b"4kph")
        crashtracker.set_runtime_version(b"v3.1.4.1")
        crashtracker.set_library_version(b"v2.7.1.8")
        crashtracker.set_stdout_filename(b"stdout.log")
        crashtracker.set_stderr_filename(b"stderr.log")
        crashtracker.set_alt_stack(False)
        crashtracker.set_resolve_frames_full()
        assert crashtracker.start()
    except Exception:
        pytest.fail("Exception when starting crashtracker")
