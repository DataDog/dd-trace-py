import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true",
        DD_REMOTE_CONFIGURATION_ENABLED="1",
    )
)
def test_auto():
    import sys

    import ddtrace.auto  # noqa:F401

    assert "threading" not in sys.modules
