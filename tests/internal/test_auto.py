import pytest


# DEV: This test must pass ALWAYS. If this test fails, it means that something
# needs to be fixed somewhere. Please DO NOT skip this test under any
# circumstance!
@pytest.mark.subprocess(
    env=dict(
        DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true",
    ),
    parametrize={
        "DD_REMOTE_CONFIGURATION_ENABLED": ("1", "0"),
    },
)
def test_auto():
    import os
    import sys

    import ddtrace.auto  # noqa:F401

    assert "threading" not in sys.modules
    assert "socket" not in sys.modules
    assert "subprocess" not in sys.modules

    if os.getenv("DD_REMOTE_CONFIGURATION_ENABLED") == "0":
        # When unloading modules we must have the HTTP clients imported already
        assert "ddtrace.internal.http" in sys.modules

        # emulate socket patching (e.g. by gevent)
        import socket  # noqa:F401

        socket.create_connection = None
        socket.socket = None

        from ddtrace.internal.http import HTTPConnection  # noqa:F401
        import ddtrace.internal.uds as uds

        assert HTTPConnection("localhost")._create_connection is not None
        assert uds.socket.socket is not None
