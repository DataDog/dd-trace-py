from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from tests.appsec.iast.iast_utils import _iast_patched_module


mod_socket = _iast_patched_module("tests.appsec.integrations.fixtures.patch_socket", should_patch_iast=True)
mod_urllib3 = _iast_patched_module("tests.appsec.integrations.fixtures.patch_urllib3", should_patch_iast=True)


def test_socketpair_roundtrip():
    """Exercise socket open/send/recv/close lifecycle and assert untainted result."""
    value = mod_socket.socketpair_roundtrip()
    assert value == "OK:True"
    assert not get_tainted_ranges(value)
    assert not is_pyobject_tainted(value)


def test_urllib3_poolmanager_open_clear():
    """Exercise urllib3 PoolManager open/clear lifecycle and assert untainted result."""
    value = mod_urllib3.urllib3_poolmanager_open_clear()
    assert value == "OK:True"
    assert not get_tainted_ranges(value)
    assert not is_pyobject_tainted(value)


def test_gevent_urllib3_poolmanager():
    """If gevent is available, exercise gevent.patch_all + urllib3 PoolManager and assert untainted result."""
    mod_gevent = _iast_patched_module("tests.appsec.integrations.fixtures.patch_gevent", should_patch_iast=True)
    value = mod_gevent.gevent_urllib3_poolmanager()
    assert value == "OK:True"
    assert not get_tainted_ranges(value)
    assert not is_pyobject_tainted(value)
