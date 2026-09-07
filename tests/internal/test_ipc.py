import os
from pathlib import Path

import pytest

from ddtrace.internal import ipc
from ddtrace.internal.ipc import SharedStringFile


def test_shared_string_file_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ipc, "TMPDIR", tmp_path)

    ssf = SharedStringFile("roundtrip")
    assert ssf.put("hello")
    assert ssf.put("world")
    assert ssf.peekall() == ["hello", "world"]
    assert ssf.snatchall() == ["hello", "world"]
    assert ssf.peekall() == []


def test_shared_string_file_uncreatable(tmp_path, monkeypatch):
    # A shared file we cannot even create degrades to a no-op instead of raising.
    # A regular file standing in for the temp dir gives ENOTDIR, which -- unlike
    # a read-only directory -- root cannot bypass either.
    blocker = tmp_path / "not-a-dir"
    blocker.touch()
    monkeypatch.setattr(ipc, "TMPDIR", blocker)

    ssf = SharedStringFile("nope")

    assert ssf.filename is None
    assert not ssf.put("hello")
    assert ssf.peekall() == []
    assert ssf.snatchall() == []
    with ssf.lock_exclusive() as f:
        assert ssf.peekall_unlocked(f) == []


@pytest.mark.parametrize("mode", ["r+b", "rb"])
def test_shared_string_file_permission_error_disables(tmp_path, monkeypatch, mode):
    # A file we cannot open (e.g. owned by another user after dropping
    # privileges) must not make callers fail, and must not be retried.
    monkeypatch.setattr(ipc, "TMPDIR", tmp_path)
    ssf = SharedStringFile("denied")
    assert ssf.put("hello")

    calls = []

    def denied(path, _mode):
        calls.append(path)
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(ipc, "open_file", denied)

    lock = ssf.lock_exclusive if mode == "r+b" else ssf.lock_shared
    with lock() as f:
        assert ssf.peekall_unlocked(f) == []

    assert ssf.filename is None
    assert len(calls) == 1

    # No further attempts to open the file are made.
    assert ssf.peekall() == []
    assert not ssf.put("world")
    with lock() as f:
        assert ssf.peekall_unlocked(f) == []
    assert len(calls) == 1


def test_shared_string_file_transient_error_not_disabling(tmp_path, monkeypatch):
    # A transient failure degrades the current operation only.
    monkeypatch.setattr(ipc, "TMPDIR", tmp_path)
    ssf = SharedStringFile("transient")
    assert ssf.put("hello")

    filename = ssf.filename
    real_open_file = ipc.open_file

    def missing(path, mode):
        raise FileNotFoundError(2, "No such file or directory", path)

    monkeypatch.setattr(ipc, "open_file", missing)
    assert ssf.peekall() == []
    assert ssf.filename == filename

    monkeypatch.setattr(ipc, "open_file", real_open_file)
    assert ssf.peekall() == ["hello"]


@pytest.mark.skipif(
    not hasattr(os, "getuid") or os.getuid() == 0,
    reason="needs POSIX permissions enforced against a non-root user",
)
def test_shared_string_file_existing_file_not_owned(tmp_path, monkeypatch):
    # The real-world scenario: the shared file already exists and the current
    # process has no permission on it.
    monkeypatch.setattr(ipc, "TMPDIR", tmp_path)
    path = tmp_path / "unowned"
    path.touch()
    os.chmod(path, 0o000)

    ssf = SharedStringFile("unowned")
    assert ssf.filename is None or Path(ssf.filename) == path

    # Whatever failed, no exception escapes to the caller.
    assert ssf.peekall() == []
    assert not ssf.put("hello")
    with ssf.lock_exclusive() as f:
        assert ssf.peekall_unlocked(f) == []
    assert ssf.filename is None


# errno 13 makes OSError construct a PermissionError, so the transient case uses
# EIO to stay a plain OSError and exercise the other branch of _try_open.
@pytest.mark.parametrize("errno_", [13, 5], ids=["permission-denied", "transient"])
def test_shared_string_file_fallback_write_reports_failure(tmp_path, monkeypatch, errno_):
    # A write that lands in the fallback must report failure. Callers such as
    # Config._add_extra_service memoize on a True return and never retry, so
    # claiming success would silently drop the value for good.
    monkeypatch.setattr(ipc, "TMPDIR", tmp_path)
    ssf = SharedStringFile("fallback")

    def failing(path, mode):
        raise OSError(errno_, "nope", path)

    monkeypatch.setattr(ipc, "open_file", failing)

    assert not ssf.put("hello")
    with ssf.lock_exclusive() as f:
        assert not ssf.put_unlocked(f, "hello")

    # And nothing was actually stored: the real file is still empty.
    monkeypatch.undo()
    monkeypatch.setattr(ipc, "TMPDIR", tmp_path)
    assert SharedStringFile("fallback").peekall() == []
