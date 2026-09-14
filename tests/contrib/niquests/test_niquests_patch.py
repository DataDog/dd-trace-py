import niquests
import pytest

from ddtrace.contrib.internal.niquests.patch import get_version
from ddtrace.contrib.internal.niquests.patch import patch
from ddtrace.contrib.internal.niquests.patch import unpatch
from ddtrace.internal.compat import is_wrapted


@pytest.fixture(autouse=True)
def reset_patch_state():
    unpatch()
    try:
        yield
    finally:
        unpatch()


def test_version_reporting():
    assert get_version() == niquests.__version__


def test_patch_and_unpatch_sync_send():
    assert not is_wrapted(niquests.Session.send)

    patch()
    assert is_wrapted(niquests.Session.send)

    unpatch()
    assert not is_wrapted(niquests.Session.send)


def test_repeated_patch_does_not_double_wrap_sync_send():
    patch()
    patch()

    assert is_wrapted(niquests.Session.send)
    assert not is_wrapted(niquests.Session.send.__wrapped__)


def test_patch_and_unpatch_async_send():
    if not hasattr(niquests, "AsyncSession"):
        pytest.skip("AsyncSession requires niquests>=3.14")

    assert not is_wrapted(niquests.AsyncSession.send)

    patch()
    assert is_wrapted(niquests.AsyncSession.send)

    unpatch()
    assert not is_wrapted(niquests.AsyncSession.send)
