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


def run_process(
    *args: str,
    env: Optional[Mapping[str, Optional[str]]] = None,
    timeout: Optional[float] = DEFAULT_TIMEOUT,
    **kwargs: Any,
) -> ProcessResult:
    """Run a command without changing its session, process group, or working directory."""
    kwargs.setdefault("close_fds", sys.platform != "win32")
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

    def pytest_configure(self, config: pytest.Config) -> None:
        pytest.register_assert_rewrite("tests", self.module_name)

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
            "no:terminal",
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


SUBPROC_TEST_ATTR = "_subproc_test"
SUBPROC_TEST_ENV_ATTR = "_subproc_test_env"
SUBPROC_ENV_VAR = "SUBPROCESS_TEST"
SUBPROC_USE_PYTEST = "_use_pytest"


def run_in_subprocess(*args, **kwargs):
    """
    Marks a test case that is to be run in its own 'clean' interpreter instance.

    When applied to a SubprocessTestCase class, each method will be run in a
    separate interpreter instance.

    When applied only to a method of a SubprocessTestCase, only the method
    will be run in a separate interpreter instance.

    Usage on a class::

        from tests.subprocesstest import SubprocessTestCase, run_in_subprocess

        @run_in_subprocess
        class PatchTests(SubprocessTestCase):
            # will be run in new interpreter
            def test_patch_before_import(self):
                patch()
                import module

            # will be run in new interpreter as well
            def test_patch_after_import(self):
                import module
                patch()


    Usage on a test method::

        class OtherTests(SubprocessTestCase):
            @run_in_subprocess
            def test_case(self):
                # Run in subprocess
                pass

            def test_case(self):
                # NOT run in subprocess
                pass

    :param env_override: dict of environment variables to provide to the subprocess.
    :return:
    """
    env_overrides = kwargs.get("env_overrides")
    use_pytest = kwargs.get("use_pytest")

    def wrapper(obj):
        setattr(obj, SUBPROC_TEST_ATTR, True)
        if env_overrides is not None:
            setattr(obj, SUBPROC_TEST_ENV_ATTR, env_overrides)
            setattr(obj, SUBPROC_USE_PYTEST, use_pytest)
        return obj

    # Support both @run_in_subprocess and @run_in_subprocess(env_overrides=...) usage
    if len(args) == 1 and callable(args[0]):
        return wrapper(args[0])
    else:
        return wrapper


class SubprocessTestCase(unittest.TestCase):
    run_in_subprocess = staticmethod(run_in_subprocess)

    def _full_method_name(self, use_pytest=False):
        test = getattr(self, self._testMethodName)
        # DEV: we have to use the internal self reference of the bound test
        # method to pull out the class and module since using a mix of `self`
        # and the test attributes will result in inconsistencies when the test
        # method is defined on another class.
        # A concrete case of this is a parent and child TestCase where the child
        # doesn't override a parent test method. The full_method_name we want
        # is that of the child test method (even though it exists on the parent).
        # This is only true if the test method is bound by pytest; pytest>=5.4 returns a function.
        if inspect.ismethod(test):
            modpath = test.__self__.__class__.__module__
            clsname = test.__self__.__class__.__name__
        else:
            modpath = self.__class__.__module__
            clsname = self.__class__.__name__
        testname = test.__name__
        format_string = "{}.py::{}::{}" if use_pytest else "{}.{}.{}"
        modpath = modpath.replace(".", "/" if use_pytest else ".")
        testcase_name = format_string.format(modpath, clsname, testname)
        return testcase_name

    def _run_test_in_subprocess(self, result):
        # Copy the environment and include the special subprocess environment
        # variable for the subprocess to detect.
        env_overrides = self._get_env_overrides()
        use_pytest = self._get_use_pytest()
        full_testcase_name = self._full_method_name(use_pytest=use_pytest)
        sp_test_env = os.environ.copy()
        sp_test_env.update(env_overrides)
        sp_test_env[SUBPROC_ENV_VAR] = "True"
        test_framework = "pytest" if use_pytest else "unittest"
        sp_test_cmd = ["python", "-m", test_framework, full_testcase_name]
        sp = subprocess.Popen(
            sp_test_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=sp_test_env,
        )
        stdout, stderr = sp.communicate()
        stdout = stdout.decode()
        stderr = stderr.decode()

        if sp.returncode and "_pytest.outcomes.xfailed" not in stderr.lower():
            try:
                cmdf = " ".join(sp_test_cmd)
                msg = f'Subprocess Test "{cmdf}" Failed (exit code {sp.returncode}):\n{stderr}'
                raise Exception(msg)
            except Exception:
                exc_info = sys.exc_info()

            # DEV: stderr, stdout are byte sequences so to print them nicely
            #      back out they should be decoded.
            sys.stderr.write(stderr)
            sys.stdout.write(stdout)
            result.addFailure(self, exc_info)
        else:
            result.addSuccess(self)

    def _in_subprocess(self):
        """Determines if the test is being run in a subprocess.

        This is done by checking for an environment variable that we call the
        subprocess test with.

        :return: whether the test is a subprocess test
        """
        return os.getenv(SUBPROC_ENV_VAR, None) is not None

    def _is_subprocess_test(self):
        if hasattr(self, SUBPROC_TEST_ATTR):
            return True

        test = getattr(self, self._testMethodName)
        if hasattr(test, SUBPROC_TEST_ATTR):
            return True

        return False

    def _get_env_overrides(self):
        if hasattr(self, SUBPROC_TEST_ENV_ATTR):
            return getattr(self, SUBPROC_TEST_ENV_ATTR)

        test = getattr(self, self._testMethodName)
        if hasattr(test, SUBPROC_TEST_ENV_ATTR):
            return getattr(test, SUBPROC_TEST_ENV_ATTR)

        return {}

    def _get_use_pytest(self):
        if hasattr(self, SUBPROC_USE_PYTEST):
            return getattr(self, SUBPROC_USE_PYTEST)

        test = getattr(self, self._testMethodName)
        if hasattr(test, SUBPROC_USE_PYTEST):
            return getattr(test, SUBPROC_USE_PYTEST)

        return False

    def run(self, result=None):
        if not self._is_subprocess_test():
            return super().run(result=result)

        if self._in_subprocess():
            return super().run(result=result)
        else:
            self._run_test_in_subprocess(result)
