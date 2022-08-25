import pytest

from ddtrace.internal.remoteconfig import RemoteConfig


def test_remote_config_register_auto_enable():
    assert RemoteConfig._worker is None

    RemoteConfig.register("LIVE_DEBUGGER", lambda m, c: None)

    assert RemoteConfig._worker is not None

    RemoteConfig.disable()

    assert RemoteConfig._worker is None


@pytest.mark.subprocess
def test_remote_config_forksafe():
    import os

    from ddtrace.internal.remoteconfig import RemoteConfig

    RemoteConfig.enable()

    parent_worker = RemoteConfig._worker
    assert parent_worker is not None

    if os.fork() == 0:
        assert RemoteConfig._worker is not None
        assert RemoteConfig._worker is not parent_worker
        exit(0)
