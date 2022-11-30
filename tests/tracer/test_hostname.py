import mock
import pytest

from ddtrace.internal.hostname import _reset
from ddtrace.internal.hostname import get_hostname


@pytest.fixture(autouse=True)
def reset_hostname():
    # Ensure _hostname is not set
    _reset()
    try:
        yield
    finally:
        _reset()


@mock.patch("socket.gethostname")
def test_get_hostname(socket_gethostname):
    # Test that `get_hostname()` just returns `socket.gethostname`
    socket_gethostname.return_value = "test-hostname"
    assert get_hostname() == "test-hostname"

    # Change the value returned by `socket.gethostname` to test the cache
    socket_gethostname.return_value = "new-hostname"
    assert get_hostname() == "test-hostname"
