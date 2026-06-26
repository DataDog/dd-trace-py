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


def test_asyncio_event_loop_registration_with_module_cloning():
    import os
    import subprocess
    import sys

    # Run in a pristine interpreter (not pytest's, which pre-imports asyncio) so that
    # asyncio is imported during ddtrace setup and is a genuine candidate for cleanup.
    # DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE forces module cloning without gevent installed.
    script = (
        "import ddtrace.auto\n"
        "import asyncio\n"
        "loop = asyncio.new_event_loop()\n"
        "asyncio.set_event_loop(loop)\n"
        "assert asyncio.get_event_loop() is loop\n"
        "print('OK')\n"
    )
    env = {**os.environ, "DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE": "1"}
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
