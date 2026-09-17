from inspect import currentframe
from inspect import getframeinfo
from pathlib import Path
import traceback

import mock
import pytest

import ddtrace.appsec._common_module_patches as cmp
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.appsec._contrib.filesystem import patch as filesystem_patch
from ddtrace.appsec._contrib.filesystem.patch import wrapped_builtin_open
from ddtrace.appsec._contrib.filesystem.patch import wrapped_path_open
from ddtrace.appsec._utils import DDWaf_result
from ddtrace.appsec._utils import _observator
from ddtrace.internal._exceptions import BlockingException


class _OriginalOpen:
    def __init__(self) -> None:
        self.called = False

    def __call__(self, *args: object, **kwargs: object) -> object:
        self.called = True
        return "opened"


class _CountingPath:
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.calls = 0

    def __fspath__(self) -> str:
        self.calls += 1
        return self.filename


class _ProxiedPath:
    def __init__(self) -> None:
        self.calls = 0

    def __fspath__(self) -> str:
        self.calls += 1
        return "actual.txt"

    def __getattribute__(self, name: str) -> object:
        if name == "__fspath__":
            return lambda: "alias.txt"
        return super().__getattribute__(name)


def test_builtin_open_skips_filename_extraction_when_capability_disabled() -> None:
    original = _OriginalOpen()
    filename = _CountingPath("example.txt")

    with mock.patch.object(filesystem_patch, "get_rasp_capability", return_value=False):
        result = wrapped_builtin_open(original, None, (filename,), {})

    assert result == "opened"
    assert original.called
    assert filename.calls == 0


def test_builtin_open_calls_handle_lfi_with_extracted_filename() -> None:
    original = _OriginalOpen()
    filename = _ProxiedPath()

    with (
        mock.patch.object(filesystem_patch, "get_rasp_capability", return_value=True),
        mock.patch.object(filesystem_patch, "handle_lfi") as handle_lfi,
    ):
        result = wrapped_builtin_open(original, None, (filename,), {})

    assert result == "opened"
    assert filename.calls == 1
    handle_lfi.assert_called_once_with("actual.txt")


def test_path_open_calls_handle_lfi_with_extracted_filename() -> None:
    original = _OriginalOpen()
    filename = Path("example.txt")

    with (
        mock.patch.object(filesystem_patch, "get_rasp_capability", return_value=True),
        mock.patch.object(filesystem_patch, "handle_lfi") as handle_lfi,
    ):
        result = wrapped_path_open(original, filename, (), {})

    assert result == "opened"
    handle_lfi.assert_called_once_with("example.txt")


def test_blocking_prevents_open() -> None:
    original = _OriginalOpen()

    with (
        mock.patch.object(filesystem_patch, "get_rasp_capability", return_value=True),
        mock.patch.object(filesystem_patch, "handle_lfi", side_effect=BlockingException("blocked")),
    ):
        with pytest.raises(BlockingException):
            wrapped_builtin_open(original, None, ("blocked.txt",), {})

    assert not original.called


def test_unexpected_exception_in_lfi_check_is_swallowed() -> None:
    """A bug in the LFI check itself must not prevent the customer's open() call from succeeding."""
    original = _OriginalOpen()

    with mock.patch.object(filesystem_patch, "get_rasp_capability", side_effect=RuntimeError("boom")):
        result = wrapped_builtin_open(original, None, ("example.txt",), {})

    assert result == "opened"
    assert original.called


def test_handle_lfi_blocks_lfi() -> None:
    result = DDWaf_result(
        1,
        [],
        {WAF_ACTIONS.BLOCK_ACTION: {}},
        0.0,
        0.0,
        False,
        _observator(),
        {},
    )
    block_config = {"status_code": 403}

    with (
        mock.patch.object(filesystem_patch, "call_waf_callback", return_value=result) as call_waf,
        mock.patch.object(filesystem_patch, "get_blocked", return_value=block_config),
        pytest.raises(BlockingException) as raised,
    ):
        filesystem_patch.handle_lfi("blocked.txt")

    call_waf.assert_called_once_with(
        {EXPLOIT_PREVENTION.ADDRESS.LFI: "blocked.txt"},
        crop_trace="handle_lfi",
        rule_type=EXPLOIT_PREVENTION.TYPE.LFI,
    )
    assert raised.value.args == (
        block_config,
        EXPLOIT_PREVENTION.BLOCKING,
        EXPLOIT_PREVENTION.TYPE.LFI,
        "blocked.txt",
    )


def test_lfi_normal_exception() -> None:
    """Ensure builtins.open exceptions start at the customer call site."""
    exception_repr = """Traceback (most recent call last):
  File "{}", line {}, in test_lfi_normal_exception
    with open("/unknown/do_not_exist_test.txt", "w"):
"""
    try:
        cmp.patch_common_modules()
        with pytest.raises(Exception) as raised:
            with open("/unknown/do_not_exist_test.txt", "w"):
                pass
        assert raised.type is FileNotFoundError
        assert raised.traceback[0].path.as_posix() == __file__
        line_number = getframeinfo(currentframe()).lineno
        try:
            with open("/unknown/do_not_exist_test.txt", "w"):
                pass
        except Exception as exc:
            assert exc.__class__.__name__ == "FileNotFoundError"
            assert exc.__traceback__.tb_frame.f_code.co_filename == __file__
            assert traceback.format_exc(limit=1).startswith(exception_repr.format(__file__, line_number + 2))
            frames = traceback.extract_tb(exc.__traceback__)
            # The caller must appear exactly once: the removed _raise_without_wrapper_frame used to
            # append a synthetic duplicate of it on top of the wrapper frame.
            assert [frame.filename for frame in frames].count(__file__) == 1
            # builtins.open is a C function and leaves no frame, so the RASP wrapper is necessarily
            # the innermost Python frame here. Removing it needs APPSEC-69877.
            assert frames[-1].name == "wrapped_builtin_open"
    finally:
        cmp.unpatch_common_modules()


def test_lfi_normal_exception_pathlib() -> None:
    """Ensure pathlib.Path.open exceptions start at the customer call site."""
    exception_repr = """Traceback (most recent call last):
  File "{}", line {}, in test_lfi_normal_exception_pathlib
    with Path("/unknown/do_not_exist_test.txt").open("w"):
"""
    try:
        cmp.patch_common_modules()
        with pytest.raises(Exception) as raised:
            with Path("/unknown/do_not_exist_test.txt").open("w"):
                pass
        assert raised.type is FileNotFoundError
        assert raised.traceback[0].path.as_posix() == __file__
        line_number = getframeinfo(currentframe()).lineno
        try:
            with Path("/unknown/do_not_exist_test.txt").open("w"):
                pass
        except Exception as exc:
            assert exc.__class__.__name__ == "FileNotFoundError"
            assert exc.__traceback__.tb_frame.f_code.co_filename == __file__
            assert traceback.format_exc(limit=1).startswith(exception_repr.format(__file__, line_number + 2))
            frames = traceback.extract_tb(exc.__traceback__)
            # The caller must appear exactly once: the removed _raise_without_wrapper_frame used to
            # append a synthetic duplicate of it on top of the wrapper frame.
            assert [frame.filename for frame in frames].count(__file__) == 1
            # Path.open is pure Python, so the frames below our wrapper are reported again; the
            # removed helper discarded them, leaving wrapped_path_open last.
            assert [frame.name for frame in frames].index("wrapped_path_open") < len(frames) - 1
            assert "pathlib" in frames[-1].filename
    finally:
        cmp.unpatch_common_modules()
