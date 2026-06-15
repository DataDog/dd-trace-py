import pytest

from ddtrace import config


@pytest.fixture(autouse=True)
def _force_pytorch_install(monkeypatch):
    """Ensure _distributed.install() runs in every pytorch test.

    The ``_should_install()`` gate skips wrapping on non-distributed processes
    (no RANK/WORLD_SIZE env, no live init_process_group). Tests need the
    wrappers installed unconditionally, so we set the escape-hatch env var
    for the whole test session. Individual tests that exercise the gate itself
    (in test_pytorch_patch.py) override or delete this env var explicitly via
    their own ``monkeypatch`` calls, which take precedence over this fixture.
    """
    monkeypatch.setenv("DD_PYTORCH_FORCE_INSTALL", "true")


@pytest.fixture
def enable_collective_trace(monkeypatch):
    """Flip `config.pytorch.collective_trace_enabled` to True for the
    duration of the test. Use this in any test that asserts on
    per-collective span emission.

    AIDEV-NOTE: We do NOT reload the integration module — sibling modules
    (`patch`, `_distributed`) are imported once at process start and
    retain their bound references to `config.pytorch.collective_trace_enabled`
    via attribute access at call time. Setting the attribute directly is
    the only reliable way to propagate the override; an `importlib.reload`
    on `ddtrace.contrib.internal.pytorch.__init__` does not touch sibling
    modules or already-bound wrapper closures.
    """
    monkeypatch.setattr(config.pytorch, "collective_trace_enabled", True)
    yield
    # `monkeypatch` restores the attribute automatically on teardown.
