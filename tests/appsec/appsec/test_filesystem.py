from inspect import currentframe
from inspect import getframeinfo
from pathlib import Path
import traceback

import mock
import pytest

import ddtrace.appsec._common_module_patches as cmp
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.appsec._contrib.filesystem import subscribers
from ddtrace.appsec._contrib.filesystem.events import FileOpenEvent
from ddtrace.appsec._contrib.filesystem.patch import wrapped_builtin_open
from ddtrace.appsec._contrib.filesystem.patch import wrapped_path_open
from ddtrace.appsec._contrib.filesystem.subscribers import AppSecFileOpenSubscriber
from ddtrace.appsec._utils import DDWaf_result
from ddtrace.appsec._utils import _observator
from ddtrace.internal import core
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


def test_builtin_open_skips_filename_extraction_without_listener() -> None:
    original = _OriginalOpen()
    filename = _CountingPath("example.txt")

    with mock.patch.object(core, "has_listeners", return_value=False):
        result = wrapped_builtin_open(original, None, (filename,), {})

    assert result == "opened"
    assert original.called
    assert filename.calls == 0


def test_builtin_open_dispatches_typed_event() -> None:
    original = _OriginalOpen()
    filename = _ProxiedPath()

    with (
        mock.patch.object(core, "has_listeners", return_value=True),
        mock.patch.object(core, "dispatch_event") as dispatch,
    ):
        result = wrapped_builtin_open(original, None, (filename,), {})

    assert result == "opened"
    assert filename.calls == 1
    dispatch.assert_called_once_with(FileOpenEvent(filename="actual.txt"), allow_raise=True)


def test_path_open_dispatches_typed_event() -> None:
    original = _OriginalOpen()
    filename = Path("example.txt")

    with (
        mock.patch.object(core, "has_listeners", return_value=True),
        mock.patch.object(core, "dispatch_event") as dispatch,
    ):
        result = wrapped_path_open(original, filename, (), {})

    assert result == "opened"
    dispatch.assert_called_once_with(FileOpenEvent(filename="example.txt"), allow_raise=True)


def test_blocking_listener_prevents_open() -> None:
    original = _OriginalOpen()

    with (
        mock.patch.object(core, "has_listeners", return_value=True),
        mock.patch.object(core, "dispatch_event", side_effect=BlockingException("blocked")),
    ):
        with pytest.raises(BlockingException):
            wrapped_builtin_open(original, None, ("blocked.txt",), {})

    assert not original.called


def test_appsec_listener_blocks_lfi() -> None:
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
    event = FileOpenEvent(filename="blocked.txt")

    with (
        mock.patch.object(subscribers, "get_rasp_capability", return_value=True),
        mock.patch.object(subscribers, "in_asm_context", return_value=True),
        mock.patch.object(subscribers, "call_waf_callback", return_value=result) as call_waf,
        mock.patch.object(subscribers, "get_blocked", return_value=block_config),
        pytest.raises(BlockingException) as raised,
    ):
        AppSecFileOpenSubscriber.on_event(event)

    call_waf.assert_called_once_with(
        {EXPLOIT_PREVENTION.ADDRESS.LFI: "blocked.txt"},
        crop_trace="on_event",
        rule_type=EXPLOIT_PREVENTION.TYPE.LFI,
    )
    assert raised.value.args == (
        block_config,
        EXPLOIT_PREVENTION.BLOCKING,
        EXPLOIT_PREVENTION.TYPE.LFI,
        "blocked.txt",
    )


def test_appsec_listener_reports_skip_outside_asm_context() -> None:
    event = FileOpenEvent(filename="example.txt")

    with (
        mock.patch.object(subscribers, "get_rasp_capability", return_value=True),
        mock.patch.object(subscribers, "in_asm_context", return_value=False),
        mock.patch.object(subscribers, "call_waf_callback") as call_waf,
        mock.patch.object(subscribers, "report_rasp_skipped") as report_skipped,
    ):
        AppSecFileOpenSubscriber.on_event(event)

    report_skipped.assert_called_once_with(EXPLOIT_PREVENTION.TYPE.LFI, False)
    call_waf.assert_not_called()


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
        assert raised.traceback[-1].path.as_posix() == __file__
        line_number = getframeinfo(currentframe()).lineno
        try:
            with open("/unknown/do_not_exist_test.txt", "w"):
                pass
        except Exception as exc:
            assert exc.__class__.__name__ == "FileNotFoundError"
            assert exc.__traceback__.tb_frame.f_code.co_filename == __file__
            assert traceback.format_exc(limit=1).startswith(exception_repr.format(__file__, line_number + 2))
            assert "_raise_without_wrapper_frame" not in (
                frame.name for frame in traceback.extract_tb(exc.__traceback__)
            )
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
        assert raised.traceback[-1].path.as_posix() == __file__
        line_number = getframeinfo(currentframe()).lineno
        try:
            with Path("/unknown/do_not_exist_test.txt").open("w"):
                pass
        except Exception as exc:
            assert exc.__class__.__name__ == "FileNotFoundError"
            assert exc.__traceback__.tb_frame.f_code.co_filename == __file__
            assert traceback.format_exc(limit=1).startswith(exception_repr.format(__file__, line_number + 2))
            assert "_raise_without_wrapper_frame" not in (
                frame.name for frame in traceback.extract_tb(exc.__traceback__)
            )
    finally:
        cmp.unpatch_common_modules()
