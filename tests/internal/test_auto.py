import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12, 5), reason="Python < 3.12.5 eagerly loads the threading module")
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
