from collections.abc import Generator
import io
from pathlib import Path
import traceback
from unittest import mock

import pytest

from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._constants import STACK_TRACE
from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.appsec._contrib.filesystem import subscribers as filesystem_subscribers
from ddtrace.appsec._contrib.filesystem.subscribers import AppSecFileOpenSubscriber
from ddtrace.appsec._utils import DDWaf_result
from ddtrace.appsec._utils import _observator
from ddtrace.contrib._events.filesystem import FileOpenEvent
from ddtrace.contrib.internal.filesystem import patch as filesystem
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.security.stack_traces import report_stack
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.telemetry import telemetry_writer
import ddtrace.internal.telemetry.writer as telemetry_writer_module
from ddtrace.trace import Span


@pytest.fixture
def patched_filesystem() -> Generator[None, None, None]:
    filesystem.patch()
    yield
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


@pytest.mark.usefixtures("patched_filesystem")
@pytest.mark.parametrize("method", ["builtin", "pathlib"])
def test_reported_stack_starts_at_application_call(tmp_path, method) -> None:
    span = Span("filesystem-stack")

    def capture_stack(addresses, *, crop_trace, rule_type):
        assert report_stack(span=span, crop_stack=crop_trace, stack_id="filesystem", namespace=STACK_TRACE.RASP)

    AppSecFileOpenSubscriber.register()
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


@pytest.mark.usefixtures("patched_filesystem")
@pytest.mark.parametrize("method", ["builtin", "pathlib"])
def test_application_exception_preserves_traceback(tmp_path: Path, method: str) -> None:
    target = tmp_path / "missing" / "file.txt"
    with pytest.raises(FileNotFoundError) as raised:
        if method == "pathlib":
            target.open("w")
        else:
            open(target, "w")

    frames = traceback.extract_tb(raised.value.__traceback__)
    assert frames[0].filename == __file__
    # The removed _raise_without_wrapper_frame used to duplicate the caller.
    assert [frame.filename for frame in frames].count(__file__) == 1
    if method == "pathlib":
        assert not any("ddtrace/contrib/internal/filesystem" in frame.filename for frame in frames)
        assert "pathlib" in frames[-1].filename
    else:
        # builtins.open is a C function and leaves no frame; removing its wrapper needs APPSEC-69877.
        assert frames[-1].name == "wrapped_builtin_open"


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


@pytest.mark.usefixtures("isolated_passthrough_wrappers", "patched_filesystem")
def test_application_open_error_through_builtin_open_wrapper_is_not_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as exc_info:
        filesystem.wrapped_builtin_open(io.open, None, (str(tmp_path / "missing"),), {})

    assert not _reported_as_ddtrace_error(exc_info.value)
