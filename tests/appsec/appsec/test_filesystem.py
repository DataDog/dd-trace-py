from collections.abc import Generator
from inspect import currentframe
from inspect import getframeinfo
import io
from pathlib import Path
import traceback
from unittest import mock

import pytest

import ddtrace.appsec._common_module_patches as cmp
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._constants import STACK_TRACE
from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.appsec._contrib.filesystem import subscribers as filesystem_subscribers
from ddtrace.appsec._contrib.filesystem.subscribers import AppSecFileOpenSubscriber
from ddtrace.appsec._utils import DDWaf_result
from ddtrace.appsec._utils import _observator
from ddtrace.contrib._events.filesystem import FileOpenEvent
from ddtrace.contrib.internal.filesystem import patch as filesystem
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.security.stack_traces import report_stack
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.telemetry import telemetry_writer
import ddtrace.internal.telemetry.writer as telemetry_writer_module
from ddtrace.trace import Span


@pytest.fixture(autouse=True)
def reset_filesystem_patches() -> Generator[None, None, None]:
    cmp.unpatch_common_modules()
    yield
    cmp.unpatch_common_modules()
    filesystem.unpatch()


class _CountingPath:
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.calls = 0

    def __fspath__(self) -> str:
        self.calls += 1
        return self.filename


class _ProxiedPath:
    def __fspath__(self) -> str:
        return "actual.txt"

    def __getattribute__(self, name: str) -> object:
        if name == "__fspath__":
            return lambda: "alias.txt"
        return super().__getattribute__(name)


def test_skips_filename_extraction_when_capability_disabled() -> None:
    filename = _CountingPath("example.txt")
    with mock.patch.object(filesystem_subscribers, "get_rasp_capability", return_value=False):
        AppSecFileOpenSubscriber.on_event(FileOpenEvent(filename))
    assert filename.calls == 0


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("example.txt", "example.txt"),
        (b"file", b"file"),
        (Path("example.txt"), "example.txt"),
        (_ProxiedPath(), "actual.txt"),
    ],
)
def test_inspects_filename(filename, expected) -> None:
    with (
        mock.patch.object(filesystem_subscribers, "get_rasp_capability", return_value=True),
        mock.patch.object(filesystem_subscribers, "call_waf_callback") as waf,
    ):
        waf.return_value = None
        AppSecFileOpenSubscriber.on_event(FileOpenEvent(filename))
    waf.assert_called_once_with(
        {EXPLOIT_PREVENTION.ADDRESS.LFI: expected}, crop_trace="on_event", rule_type=EXPLOIT_PREVENTION.TYPE.LFI
    )


@pytest.mark.parametrize("filename", [0, None, "", b""])
def test_non_paths_are_not_inspected(filename) -> None:
    with (
        mock.patch.object(filesystem_subscribers, "get_rasp_capability", return_value=True),
        mock.patch.object(filesystem_subscribers, "call_waf_callback") as waf,
    ):
        AppSecFileOpenSubscriber.on_event(FileOpenEvent(filename))
    waf.assert_not_called()


@pytest.mark.parametrize("method", ["builtin", "pathlib"])
def test_subscriber_blocks_before_open(tmp_path, method) -> None:
    filename = tmp_path / "blocked.txt"
    AppSecFileOpenSubscriber.register()
    filesystem.patch()
    try:
        with (
            mock.patch.object(filesystem_subscribers, "get_rasp_capability", return_value=True),
            mock.patch.object(filesystem_subscribers, "call_waf_callback") as waf,
        ):
            waf.return_value = mock.Mock(actions={WAF_ACTIONS.BLOCK_ACTION: {}})
            with pytest.raises(BlockingException):
                if method == "pathlib":
                    filename.open("w")
                else:
                    open(filename, "w")
        assert not filename.exists()
    finally:
        filesystem.unpatch()
        AppSecFileOpenSubscriber.unregister()


def test_registration_restores_after_disable_and_listener_reset(tmp_path) -> None:
    from ddtrace.internal.appsec import product_subscribers

    filesystem.patch()
    try:
        with (
            mock.patch.object(asm_config, "_asm_enabled", True),
            mock.patch.object(asm_config, "_ep_enabled", True),
            mock.patch.object(filesystem_subscribers, "get_rasp_capability", return_value=True),
            mock.patch.object(filesystem_subscribers, "call_waf_callback", return_value=None) as waf,
        ):
            product_subscribers.register()
            product_subscribers.register()
            with open(tmp_path / "once", "w"):
                pass
            assert waf.call_count == 1
            product_subscribers.unregister()
            assert not core.has_listeners(FileOpenEvent.event_name)
            with open(tmp_path / "disabled", "w"):
                pass
            assert waf.call_count == 1
            product_subscribers.register()
            core.reset_listeners(FileOpenEvent.event_name)
            product_subscribers.register()
            with open(tmp_path / "again", "w"):
                pass
            assert waf.call_count == 2
    finally:
        product_subscribers.unregister()
        filesystem.unpatch()


def test_other_consumer_survives_appsec_unregistration(tmp_path) -> None:
    events = []
    core.on(FileOpenEvent.event_name, events.append, name="test.independent.filesystem")
    filesystem.patch()
    AppSecFileOpenSubscriber.register()
    try:
        with (
            mock.patch.object(filesystem_subscribers, "get_rasp_capability", return_value=True),
            mock.patch.object(filesystem_subscribers, "call_waf_callback", return_value=None) as waf,
        ):
            with open(tmp_path / "active", "w"):
                pass
            assert waf.call_count == 1
            AppSecFileOpenSubscriber.unregister()
            with open(tmp_path / "other-consumer", "w"):
                pass
            assert waf.call_count == 1
            assert len(events) == 2
            AppSecFileOpenSubscriber.register()
            with open(tmp_path / "reactivated", "w"):
                pass
            assert waf.call_count == 2
            assert len(events) == 3
    finally:
        AppSecFileOpenSubscriber.unregister()
        core.reset_listeners(FileOpenEvent.event_name, events.append)
        filesystem.unpatch()


@pytest.mark.parametrize("method", ["builtin", "pathlib"])
def test_reported_stack_starts_at_application_call(tmp_path, method) -> None:
    span = Span("filesystem-stack")

    def capture_stack(addresses, *, crop_trace, rule_type):
        assert report_stack(span=span, crop_stack=crop_trace, stack_id="filesystem", namespace=STACK_TRACE.RASP)

    AppSecFileOpenSubscriber.register()
    filesystem.patch()
    try:
        with (
            mock.patch.object(filesystem_subscribers, "get_rasp_capability", return_value=True),
            mock.patch.object(filesystem_subscribers, "call_waf_callback", side_effect=capture_stack),
            mock.patch.object(asm_config, "_asm_enabled", True),
            mock.patch.object(asm_config, "_ep_enabled", True),
            mock.patch.object(asm_config, "_ep_stack_trace_enabled", True),
        ):
            target = tmp_path / "stack.txt"
            with target.open("w") if method == "pathlib" else open(target, "w"):
                pass
        frames = span._get_struct_tag(STACK_TRACE.TAG)[STACK_TRACE.RASP][0]["frames"]
        assert frames[0]["file"] == __file__
        assert frames[0]["function"].endswith("test_reported_stack_starts_at_application_call")
    finally:
        AppSecFileOpenSubscriber.unregister()
        filesystem.unpatch()


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
        mock.patch.object(filesystem_subscribers, "get_rasp_capability", return_value=True),
        mock.patch.object(filesystem_subscribers, "call_waf_callback", return_value=result) as call_waf,
        mock.patch.object(filesystem_subscribers, "get_blocked", return_value=block_config),
        pytest.raises(BlockingException) as raised,
    ):
        AppSecFileOpenSubscriber.on_event(FileOpenEvent("blocked.txt"))

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
            assert not any("ddtrace/contrib/internal/filesystem" in frame.filename for frame in frames)
            assert "pathlib" in frames[-1].filename
    finally:
        cmp.unpatch_common_modules()


@pytest.fixture
def isolated_passthrough_wrappers():
    with mock.patch.object(telemetry_writer_module, "_PASSTHROUGH_CODES", set()):
        yield


def _reported_as_ddtrace_error(exc: BaseException) -> bool:
    with mock.patch.object(telemetry_writer, "add_error_log") as add_error_log:
        with mock.patch.object(telemetry_writer, "app_shutdown") as app_shutdown:
            telemetry_writer._telemetry_excepthook(type(exc), exc, exc.__traceback__)
    app_shutdown.assert_called_once()
    return add_error_log.called


@pytest.mark.usefixtures("isolated_passthrough_wrappers")
def test_application_open_error_through_builtin_open_wrapper_is_not_reported(tmp_path: Path) -> None:
    filesystem.patch()
    with pytest.raises(FileNotFoundError) as exc_info:
        filesystem.wrapped_builtin_open(io.open, None, (str(tmp_path / "missing"),), {})

    assert not _reported_as_ddtrace_error(exc_info.value)
