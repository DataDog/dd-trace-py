import pytest


@pytest.mark.subprocess(env=dict(DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true"))
def test_auto():
    import sys

    import ddtrace.auto  # noqa

    assert "threading" not in sys.modules
