"""Process execution for test scenarios, independent of pytest and ddtrace."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryFile
import time
from typing import Any
from typing import Optional
from typing import Union
import unittest

from tests._ddtest_env_helpers import strip_ddtest_leaked_env


DEFAULT_TIMEOUT = 60.0
CLEANUP_TIMEOUT = 5.0


@dataclass(frozen=True)
class ProcessResult:
    stdout: Union[bytes, str]
    stderr: Union[bytes, str]
    returncode: int
    pid: int

    def check_returncode(self, expected: int = 0) -> ProcessResult:
        if self.returncode != expected:
            raise AssertionError(
                f"Expected exit status {expected}, got {self.returncode}.\n"
                f"STDOUT:\n{self.stdout!r}\nSTDERR:\n{self.stderr!r}"
            )
        return self


def child_environment(
    env: Optional[Mapping[str, Optional[str]]] = None,
    overrides: Optional[Mapping[str, Optional[str]]] = None,
    *,
    strip_test_options: bool = True,
) -> dict[str, str]:
    result = {key: value for key, value in (os.environ if env is None else env).items() if value is not None}
    for key, value in (overrides or {}).items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return strip_ddtest_leaked_env(result) if strip_test_options else result


def _signal(process: subprocess.Popen[Any], sig: int, descendants: bool) -> None:
    try:
        if descendants:
            os.killpg(process.pid, sig)
        elif process.poll() is None:
            process.send_signal(sig)
    except ProcessLookupError:
        pass


def run_process(
    *args: str,
    env: Optional[Mapping[str, Optional[str]]] = None,
    env_overrides: Optional[Mapping[str, Optional[str]]] = None,
    timeout: Optional[float] = DEFAULT_TIMEOUT,
    strip_test_options: bool = True,
    terminate_on_timeout: bool = False,
    **kwargs: Any,
) -> ProcessResult:
    """Run a command without changing its session, process group, or working directory."""
    kwargs.setdefault("close_fds", sys.platform != "win32")
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_environment(env, env_overrides, strip_test_options=strip_test_options),
        **kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException as error:
        _signal(process, signal.SIGTERM, False)
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
            if terminate_on_timeout:
                return ProcessResult(stdout, stderr, process.returncode, process.pid)
        raise
    return ProcessResult(stdout, stderr, process.returncode, process.pid)


class ManagedProcess:
    """A running application whose output cannot fill a pipe while its parent interacts with it."""

    def __init__(self, args: list[str], *, descendants: bool = False, **kwargs: Any) -> None:
        if descendants and os.name != "posix":
            raise ValueError("Descendant cleanup requires POSIX process groups")
        self._descendants = descendants
        stream_options: dict[str, Any] = {}
        if any(kwargs.get(option) for option in ("text", "universal_newlines", "encoding", "errors")):
            stream_options = {"mode": "w+", "encoding": kwargs.get("encoding"), "errors": kwargs.get("errors")}
        self._stdout = TemporaryFile(**stream_options)
        self._stderr = TemporaryFile(**stream_options)
        env = child_environment(
            kwargs.pop("env", None),
            kwargs.pop("env_overrides", None),
            strip_test_options=kwargs.pop("strip_test_options", True),
        )
        try:
            self.process = subprocess.Popen(
                args,
                stdout=self._stdout,
                stderr=self._stderr,
                env=env,
                start_new_session=descendants,
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
            _signal(self.process, signal.SIGTERM, self._descendants)
            try:
                self.process.wait(timeout=CLEANUP_TIMEOUT)
            except subprocess.TimeoutExpired:
                if self._descendants:
                    _signal(self.process, signal.SIGKILL, True)
                else:
                    self.process.kill()
                self.process.wait(timeout=CLEANUP_TIMEOUT)
            finally:
                if self._descendants:
                    _signal(self.process, signal.SIGKILL, True)
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
    def _command(target: str, args: tuple[str, ...], ddtrace_run: bool, module: bool = False) -> list[str]:
        return (
            (["ddtrace-run"] if ddtrace_run else []) + [sys.executable] + (["-m"] if module else []) + [target, *args]
        )

    def __call__(
        self, code: str, *, args: tuple[str, ...] = (), ddtrace_run: bool = False, **kwargs: Any
    ) -> ProcessResult:
        return self.file(self._source(code), args=args, ddtrace_run=ddtrace_run, **kwargs)

    def file(
        self, path: Union[str, Path], *, args: tuple[str, ...] = (), ddtrace_run: bool = False, **kwargs: Any
    ) -> ProcessResult:
        return run_process(*self._command(str(path), args, ddtrace_run), **kwargs)

    def module(
        self, name: str, *, args: tuple[str, ...] = (), ddtrace_run: bool = False, **kwargs: Any
    ) -> ProcessResult:
        return run_process(*self._command(name, args, ddtrace_run, module=True), **kwargs)

    @contextmanager
    def start(
        self, code: str, *, args: tuple[str, ...] = (), ddtrace_run: bool = False, **kwargs: Any
    ) -> Iterator[ManagedProcess]:
        process = ManagedProcess(self._command(str(self._source(code)), args, ddtrace_run), **kwargs)
        try:
            yield process
        finally:
            process.close()


class _TestSourceLoader(SourceFileLoader):
    def get_code(self, fullname: str) -> Any:
        filename = self.get_filename(fullname)
        return compile(self.get_data(filename), filename, "exec", dont_inherit=True, optimize=0)


class _TestAssertionFinder(MetaPathFinder):
    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if not fullname.startswith("tests."):
            return None
        spec = PathFinder.find_spec(fullname, path, target)
        if spec is not None and isinstance(spec.loader, SourceFileLoader):
            spec.loader = _TestSourceLoader(fullname, spec.loader.path)
        return spec


def enable_test_assertions() -> None:
    """Preserve assertions in child test helpers without changing product optimization."""
    if not sys.flags.optimize or any(isinstance(finder, _TestAssertionFinder) for finder in sys.meta_path):
        return
    sys.meta_path.insert(sys.meta_path.index(PathFinder), _TestAssertionFinder())


@contextmanager
def case_context(case_type: type[unittest.TestCase], method_name: str = "runTest") -> Iterator[Any]:
    """Reuse a unittest class's lifecycle inside an explicit child scenario."""
    if not __debug__ and not any(isinstance(finder, _TestAssertionFinder) for finder in sys.meta_path):
        raise RuntimeError("Class-based scenarios require Python assertions to be enabled")
    if getattr(case_type, "__unittest_skip__", False):
        raise unittest.SkipTest(getattr(case_type, "__unittest_skip_why__", "class skipped"))
    case: Any = case_type(method_name)
    loop: Any = None
    try:
        case_type.setUpClass()
        try:
            try:
                case.setUp()
                loop = getattr(case, "loop", None)
                try:
                    yield case
                finally:
                    teardown = case.tearDown()
                    if hasattr(teardown, "__await__"):
                        loop.run_until_complete(teardown)
            finally:
                if not case.doCleanups():
                    raise AssertionError("Test case cleanup failed")
        finally:
            case_type.tearDownClass()
    finally:
        try:
            case_type.doClassCleanups()
            errors = getattr(case_type, "tearDown_exceptions", ())
            if errors:
                raise errors[0][1]
        finally:
            if loop is not None and not loop.is_closed():
                loop.close()
