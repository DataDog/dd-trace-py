import pytest  # noqa: I001


@pytest.mark.subprocess()
def test_lazy_import():
    import ddtrace.auto  # noqa: F401,I001
    from ddtrace.trace import tracer  # noqa: I001

    assert tracer.context_provider.active() is None
    span = tracer.trace("itsatest", service="test", resource="resource", span_type="http")
    assert tracer.context_provider.active() is span

    # Importing asyncio after starting a trace does not remove the current active span
    import asyncio  # noqa: F401

    assert tracer.context_provider.active() is span
    span.finish()
    assert tracer.context_provider.active() is None


def test_asyncio_not_unloaded_by_module_cloning():
    import os
    import subprocess
    import sys

    # Run in a pristine interpreter (not pytest's, which pre-imports asyncio) so that
    # asyncio is imported during ddtrace setup and is a genuine candidate for cleanup.
    # DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE forces module cloning without gevent installed.
    # Unloading asyncio desyncs the re-imported module from the _asyncio C extension and
    # breaks event loop registration, so cloning must keep both modules loaded.
    script = (
        "import ddtrace.auto\n"
        "import sys\n"
        "assert 'asyncio' in sys.modules, 'asyncio was unloaded by module cloning'\n"
        "assert '_asyncio' in sys.modules, '_asyncio was unloaded by module cloning'\n"
        "print('OK')\n"
    )
    env = {**os.environ, "DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE": "1"}
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
