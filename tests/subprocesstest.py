"""Process execution for test scenarios without initializing ddtrace."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
import importlib.util
import inspect
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from tempfile import TemporaryFile
import time
from typing import Any
from typing import NamedTuple
from typing import Optional
from typing import Union
import unittest
import warnings

from _pytest.terminal import TerminalReporter
import pytest

from tests._ddtest_env_helpers import strip_ddtest_leaked_env


DEFAULT_TIMEOUT = 60.0
CLEANUP_TIMEOUT = 5.0

# Prefer the wrapper installed with the running interpreter: the first ddtrace-run found on PATH may
# belong to another environment and import a ddtrace build that does not match sys.executable.
DDTRACE_RUN: str = shutil.which("ddtrace-run", path=os.path.dirname(sys.executable)) or "ddtrace-run"


class ProcessResult(NamedTuple):
    stdout: Union[bytes, str]
    stderr: Union[bytes, str]
    returncode: int
    pid: int

    def check_returncode(self, expected: int = 0) -> None:
        if self.returncode != expected:
            raise AssertionError(
                f"Expected exit status {expected}, got {self.returncode}.\n"
                f"STDOUT:\n{self.stdout!r}\nSTDERR:\n{self.stderr!r}"
            )


def child_environment(env: Optional[Mapping[str, Optional[str]]] = None) -> dict[str, str]:
    result = {key: value for key, value in (os.environ if env is None else env).items() if value is not None}
    return strip_ddtest_leaked_env(result)


def restore_default_sigint() -> None:
    signal.signal(signal.SIGINT, signal.SIG_DFL)


def _set_default_preexec_fn(kwargs: dict[str, Any]) -> None:
    if sys.platform != "win32":
        kwargs.setdefault("preexec_fn", restore_default_sigint)


def run_process(
    *args: str,
    env: Optional[Mapping[str, Optional[str]]] = None,
    timeout: Optional[float] = DEFAULT_TIMEOUT,
    **kwargs: Any,
) -> ProcessResult:
    """Run a command without changing its session, process group, or working directory."""
    kwargs.setdefault("close_fds", sys.platform != "win32")
    _set_default_preexec_fn(kwargs)
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_environment(env),
        **kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException as error:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                stdout, stderr = process.communicate(timeout=CLEANUP_TIMEOUT)
            except subprocess.TimeoutExpired as remaining:
                stdout, stderr = remaining.output, remaining.stderr
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
                process.wait(timeout=CLEANUP_TIMEOUT)
        if isinstance(error, subprocess.TimeoutExpired):
            error.output, error.stderr = stdout, stderr
        raise
    return ProcessResult(stdout, stderr, process.returncode, process.pid)


class ManagedProcess:
    """A running application whose output cannot fill a pipe while its parent interacts with it."""

    def __init__(self, args: list[str], **kwargs: Any) -> None:
        self._stdout = TemporaryFile()
        self._stderr = TemporaryFile()
        env = child_environment(kwargs.pop("env", None))
        _set_default_preexec_fn(kwargs)
        try:
            self.process = subprocess.Popen(
                args,
                stdout=self._stdout,
                stderr=self._stderr,
                env=env,
                **kwargs,
            )
        except BaseException:
            self._stdout.close()
            self._stderr.close()
            raise

    @property
    def pid(self) -> int:
        return self.process.pid

    def _result(self) -> ProcessResult:
        self._stdout.seek(0)
        self._stderr.seek(0)
        return ProcessResult(self._stdout.read(), self._stderr.read(), self.process.returncode, self.pid)

    def wait(self, timeout: float = DEFAULT_TIMEOUT) -> ProcessResult:
        self.process.wait(timeout=timeout)
        return self._result()

    def wait_until(self, ready: Callable[[], bool], timeout: float = DEFAULT_TIMEOUT) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(f"Application exited before becoming ready: {self._result()!r}")
            if ready():
                return
            time.sleep(0.01)
        raise TimeoutError(f"Application {self.pid} did not become ready within {timeout}s")

    def close(self) -> None:
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=CLEANUP_TIMEOUT)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=CLEANUP_TIMEOUT)
        finally:
            self._stdout.close()
            self._stderr.close()


class PythonProcess:
    """Write application source under a stable service-name directory and run it."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "__init__.py").touch()
        self._sequence = 0

    def _source(self, code: str) -> Path:
        self._sequence += 1
        path = self.directory / ("test.py" if self._sequence == 1 else f"test_{self._sequence}.py")
        path.write_text(code, encoding="utf-8")
        return path

    @staticmethod
    def _command(target: str, args: tuple[str, ...], ddtrace_run: bool) -> list[str]:
        return ([DDTRACE_RUN] if ddtrace_run else []) + [sys.executable] + [target, *args]

    def __call__(
        self, code: str, *, args: tuple[str, ...] = (), ddtrace_run: bool = False, **kwargs: Any
    ) -> ProcessResult:
        return run_process(*self._command(str(self._source(code)), args, ddtrace_run), **kwargs)

    @contextmanager
    def start(
        self, code: str, *, args: tuple[str, ...] = (), ddtrace_run: bool = False, **kwargs: Any
    ) -> Iterator[ManagedProcess]:
        with self.start_file(self._source(code), args=args, ddtrace_run=ddtrace_run, **kwargs) as process:
            yield process

    @contextmanager
    def start_file(
        self, path: Union[str, Path], *, args: tuple[str, ...] = (), ddtrace_run: bool = False, **kwargs: Any
    ) -> Iterator[ManagedProcess]:
        process = ManagedProcess(self._command(str(path), args, ddtrace_run), **kwargs)
        try:
            yield process
        finally:
            process.close()


class _ScenarioReports:
    def __init__(self, module_name: str, class_name: str, method_name: str) -> None:
        self.module_name = module_name
        self.target = [*class_name.split("."), method_name]
        self.reports: list[pytest.TestReport] = []

    @pytest.hookimpl(trylast=True)
    def pytest_configure(self, config: pytest.Config) -> None:
        pytest.register_assert_rewrite("tests", self.module_name)
        # Assertion explanations need the terminal reporter, but the parent compares the child's
        # stdout with the expected output. Replace the reporter with one that discards its output.
        terminal = open(os.devnull, "w")
        config.add_cleanup(terminal.close)
        config.pluginmanager.unregister(name="terminalreporter")
        config.pluginmanager.register(TerminalReporter(config, terminal), "terminalreporter")

    @pytest.hookimpl(tryfirst=True)
    def pytest_pycollect_makeitem(self, collector: pytest.Collector, name: str) -> Any:
        depth = sum(isinstance(node, pytest.Class) for node in collector.listchain())
        if name != self.target[depth]:
            return []
        return None

    def pytest_collection_modifyitems(self, items: list[pytest.Function]) -> None:
        for item in items:
            if (
                issubclass(item.cls, unittest.TestCase)
                and inspect.iscoroutinefunction(item.obj)
                and not issubclass(item.cls, unittest.IsolatedAsyncioTestCase)
            ):
                raise TypeError("Async unittest methods must inherit from unittest.IsolatedAsyncioTestCase")
            # Only pytest's unittest/xunit lifecycle fixtures run in the child.
            item.fixturenames[:] = [
                name for name in item.fixturenames if name.lstrip("_").startswith(("xunit_", "unittest_"))
            ]

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.reports.append(report)
        if report.failed:
            sys.stderr.write(report.longreprtext + "\n")

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            sys.stderr.write(report.longreprtext + "\n")

    def pytest_internalerror(self, excrepr: Any) -> bool:
        # The replacement terminal reporter discards internal errors. Write them to stderr in the
        # same format that pytest uses when no plugin reports them.
        for line in str(excrepr).split("\n"):
            sys.stderr.write(f"INTERNALERROR> {line}\n")
        return True


def run_scenario(module_name: str, class_name: str, method_name: str) -> None:
    """Run one method with pytest, without project fixtures or auto-loaded plugins."""
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.origin is None:
        raise ImportError(module_name)
    reports = _ScenarioReports(module_name, class_name, method_name)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    # The parent validates markers with its plugins; the isolated child loads none.
    warnings.filterwarnings("ignore", category=pytest.PytestUnknownMarkWarning)
    status = pytest.main(
        [
            f"{spec.origin}::{class_name.replace('.', '::')}::{method_name}",
            "-c",
            os.devnull,
            "--noconftest",
            "-p",
            "no:warnings",
            "-p",
            "no:logging",
            "-p",
            "no:cacheprovider",
            "-s",
            "-o",
            "addopts=",
        ],
        plugins=[reports],
    )
    unittest.case.doModuleCleanups()
    if status:
        raise SystemExit(1)
    for report in reports.reports:
        if report.skipped:
            sys.stdout.write(str(report.longrepr))
            raise SystemExit(78 if hasattr(report, "wasxfail") else 77)


def check_scenario_outcome(result: ProcessResult) -> None:
    if result.returncode == 77:
        pytest.skip(result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout)
    if result.returncode == 78:
        pytest.xfail(result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout)


def run_in_subprocess(*args, env_overrides=None):
    # TODO(mabdinur): remove and replace with @pytest.mark.subprocess(env=...).
    return pytest.mark.subprocess(env=env_overrides or {})(*args)


class SubprocessTestCase(unittest.TestCase):
    # TODO(mabdinur): remove and replace with unittest.TestCase and @pytest.mark.subprocess.
    run_in_subprocess = staticmethod(run_in_subprocess)
