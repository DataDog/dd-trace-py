import builtins
from collections.abc import Generator
import os
from pathlib import Path
from unittest import mock

import pytest
from wrapt import FunctionWrapper

from ddtrace import patch
from ddtrace.contrib._events.filesystem import FileOpenEvent
from ddtrace.contrib.internal.filesystem import patch as filesystem
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException


@pytest.fixture
def events() -> Generator[list[FileOpenEvent], None, None]:
    captured: list[FileOpenEvent] = []
    core.on(FileOpenEvent.event_name, captured.append, name="test.filesystem")
    filesystem.patch()
    try:
        yield captured
    finally:
        filesystem.unpatch()
        core.reset_listeners(FileOpenEvent.event_name, captured.append)


@pytest.mark.parametrize("method", ["builtin", "keyword", "pathlib"])
def test_open_publishes_before_creating_file(tmp_path: Path, events: list[FileOpenEvent], method: str) -> None:
    target = tmp_path / "new.txt"

    def before_open(event: FileOpenEvent) -> None:
        assert event.filename == str(target)
        assert not target.exists()

    core.on(FileOpenEvent.event_name, before_open)
    try:
        if method == "pathlib":
            stream = target.open("w")
        elif method == "keyword":
            stream = open(file=str(target), mode="w")
        else:
            stream = open(str(target), "w")
        with stream:
            stream.write("contents")
    finally:
        core.reset_listeners(FileOpenEvent.event_name, before_open)
    assert target.exists()
    assert events


@pytest.mark.parametrize("method", ["builtin", "pathlib"])
def test_block_prevents_side_effect(tmp_path: Path, events: list[FileOpenEvent], method: str) -> None:
    target = tmp_path / "blocked.txt"
    blocked = BlockingException("blocked")

    def block(event: FileOpenEvent) -> None:
        raise blocked

    core.on(FileOpenEvent.event_name, block)
    try:
        with pytest.raises(BlockingException) as raised:
            if method == "pathlib":
                target.open("w")
            else:
                open(target, "w")
        assert raised.value is blocked
        assert not target.exists()
    finally:
        core.reset_listeners(FileOpenEvent.event_name, block)


def test_listener_failure_does_not_prevent_open(tmp_path: Path, events: list[FileOpenEvent]) -> None:
    def fail(event: FileOpenEvent) -> None:
        raise RuntimeError("inspection failed")

    core.on(FileOpenEvent.event_name, fail)
    try:
        with (tmp_path / "ok.txt").open("w") as stream:
            stream.write("ok")
    finally:
        core.reset_listeners(FileOpenEvent.event_name, fail)


@pytest.mark.parametrize("path", ["file.txt", b"file.txt", Path("file.txt"), 0, None])
def test_filename_protocol(path: object) -> None:
    event = FileOpenEvent(path)
    expected = os.fspath(path) if isinstance(path, (str, bytes, os.PathLike)) else None
    assert event.filename == expected


def test_no_event_allocated_without_listeners() -> None:
    original = mock.Mock(return_value="opened")
    with (
        mock.patch.object(core, "has_listeners", return_value=False),
        mock.patch.object(filesystem, "FileOpenEvent", wraps=FileOpenEvent) as event,
    ):
        assert filesystem.wrapped_builtin_open(original, None, ("file.txt",), {}) == "opened"
    event.assert_not_called()


def test_patch_cycles_and_bytecode_wrapping() -> None:
    filesystem.unpatch()
    original_open = builtins.open
    original_path_open = Path.open
    try:
        patch(filesystem=True)
        wrapped_open = builtins.open
        filesystem.patch()
        assert builtins.open is wrapped_open
        assert Path.open is original_path_open
        assert filesystem._PathOpenContext.is_wrapped(Path.open)
        assert not isinstance(Path.open, FunctionWrapper)
    finally:
        filesystem.unpatch()
    filesystem.unpatch()
    assert builtins.open is original_open
    assert not filesystem._PathOpenContext.is_wrapped(Path.open)


def test_unpatch_preserves_other_builtin_wrapper() -> None:
    filesystem.unpatch()
    original = builtins.open
    filesystem.patch()
    outer = FunctionWrapper(builtins.open, lambda wrapped, instance, args, kwargs: wrapped(*args, **kwargs))
    builtins.open = outer
    try:
        filesystem.unpatch()
        assert builtins.open is outer
        assert outer.__wrapped__ is original
    finally:
        builtins.open = original


@pytest.mark.subprocess(env={"DD_APPSEC_ENABLED": "false", "DD_IAST_ENABLED": "false"})
def test_manual_patch_does_not_import_appsec() -> None:
    import os
    import sys

    from ddtrace import patch
    from ddtrace.contrib.internal.filesystem.patch import unpatch

    patch(filesystem=True)
    try:
        with open(os.devnull) as stream:
            assert stream.read() == ""
        assert "ddtrace.appsec._processor" not in sys.modules
        assert "ddtrace.appsec._contrib.filesystem.subscribers" not in sys.modules
    finally:
        unpatch()
