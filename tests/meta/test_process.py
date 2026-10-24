import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest

import pytest

from tests.process import case_context
from tests.process import child_environment
from tests.process import run_process


def test_runner_import_does_not_initialize_ddtrace():
    result = run_process(sys.executable, "-c", "import tests.process, sys; assert 'ddtrace' not in sys.modules")
    result.check_returncode()


def test_environment_overlay_and_replacement(monkeypatch):
    monkeypatch.setenv("PROCESS_PARENT", "parent")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--ddtrace")
    assert child_environment({}, {"CHILD": "child"}) == {"CHILD": "child"}
    env = child_environment(overrides={"PROCESS_PARENT": None, "CHILD": "child"})
    assert "PROCESS_PARENT" not in env
    assert "PYTEST_ADDOPTS" not in env
    assert env["CHILD"] == "child"
    assert os.environ["PROCESS_PARENT"] == "parent"
    assert child_environment({"PYTEST_ADDOPTS": "--help"}, strip_test_options=False) == {"PYTEST_ADDOPTS": "--help"}


def test_result_and_expected_failure(python_process):
    result = python_process("import sys; sys.stdout.buffer.write(b'\\xff'); sys.stderr.write('failure'); sys.exit(7)")
    assert result.stdout == b"\xff"
    assert result.stderr == b"failure"
    result.check_returncode(7)
    with pytest.raises(AssertionError, match="failure"):
        result.check_returncode()


def test_text_output(python_process):
    result = python_process("print('hello')", text=True)
    assert result.stdout == "hello\n"
    assert result.stderr == ""


def test_managed_text_output(python_process):
    with python_process.start(
        "import sys; sys.stdout.buffer.write(b'caf\\xe9\\r\\n'); sys.stderr.write('error')",
        text=True,
        encoding="latin-1",
    ) as child:
        result = child.wait()
    result.check_returncode()
    assert result.stdout == "café\n"
    assert result.stderr == "error"


def test_source_file_module_arguments_and_cwd(python_process, tmp_path):
    result = python_process("import sys; print(sys.argv[1])", args=("hello",))
    assert result.stdout == b"hello\n"
    path = tmp_path / "application.py"
    path.write_text("import os; print(os.getcwd())")
    result = python_process.file(path, cwd=tmp_path)
    assert result.stdout.decode().strip() == str(tmp_path)
    result = python_process.module("application", cwd=tmp_path)
    assert result.stdout.decode().strip() == str(tmp_path)


def test_timeout_is_failure_and_reaps_child(python_process, tmp_path):
    pidfile = tmp_path / "pid"
    code = f"import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired):
        python_process(code, timeout=1)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_managed_cleanup_on_parent_failure(python_process, tmp_path):
    ready = tmp_path / "ready"
    with pytest.raises(ValueError, match="parent"):
        with python_process.start(
            f"import pathlib, time; pathlib.Path({str(ready)!r}).touch(); time.sleep(30)"
        ) as child:
            child.wait_until(ready.exists)
            pid = child.pid
            raise ValueError("parent")
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_managed_output_does_not_block(python_process):
    with python_process.start("import sys; sys.stdout.write('x' * 1000000)") as child:
        result = child.wait()
        assert result.stdout == b"x" * 1000000
        result.check_returncode()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_managed_kills_child_ignoring_term(python_process, tmp_path, monkeypatch):
    monkeypatch.setattr("tests.process.CLEANUP_TIMEOUT", 0.1)
    ready = tmp_path / "ready"
    with python_process.start(
        f"import signal, pathlib, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(ready)!r}).touch(); time.sleep(30)"
    ) as child:
        child.wait_until(ready.exists)
    assert child.process.returncode == -signal.SIGKILL


def test_child_case_setup_teardown_and_cleanup():
    calls = []

    class Case(unittest.TestCase):
        def setUp(self):
            calls.append("setup")
            self.addCleanup(calls.append, "cleanup")

        def tearDown(self):
            calls.append("teardown")

    with pytest.raises(ValueError):
        with case_context(Case):
            calls.append("body")
            raise ValueError()
    assert calls == ["setup", "body", "teardown", "cleanup"]


@pytest.mark.parametrize("launcher", [False, True])
def test_child_and_grandchild_coverage(python_process, tmp_path, launcher):
    import coverage

    startup = tmp_path / "startup"
    startup.mkdir()
    (startup / "sitecustomize.py").write_text("import coverage; coverage.process_startup()\n")
    config = tmp_path / "coverage.ini"
    data = tmp_path / ".coverage"
    config.write_text(f"[run]\nparallel = true\ndata_file = {data}\nsource = ddtrace\n")
    env = {
        "COVERAGE_PROCESS_START": str(config),
        "COVERAGE_FILE": str(data),
        "PYTHONPATH": os.pathsep.join([str(startup), str(Path.cwd()), os.environ.get("PYTHONPATH", "")]),
    }
    code = "from ddtrace.internal import glob_matching; print(glob_matching.__file__); "
    code += "assert glob_matching.GlobMatcher('a*').match('abc')"
    result = python_process(
        f"import subprocess, sys; children = [subprocess.Popen([sys.executable, '-c', {code!r}], "
        "stdout=subprocess.PIPE) for _ in range(2)]; "
        "output = [child.communicate()[0] for child in children]; "
        "assert all(child.returncode == 0 for child in children); sys.stdout.buffer.write(b''.join(output))",
        ddtrace_run=launcher,
        env_overrides=env,
    ).check_returncode()
    assert len(list(tmp_path.glob(".coverage.*"))) >= 3
    measured = coverage.Coverage(config_file=str(config))
    measured.combine()
    measured.load()
    paths = result.stdout.decode().splitlines()
    assert len(paths) == 2 and paths[0] == paths[1], result
    path = str(Path(paths[0]).resolve())
    lines = measured.get_data().lines(path)
    assert lines is not None, (path, sorted(measured.get_data().measured_files()))
    assert 20 in lines


def test_child_case_awaits_teardown_on_its_own_loop():
    import asyncio

    calls = []

    class Case(unittest.TestCase):
        def setUp(self):
            self.loop = asyncio.new_event_loop()

        async def tearDown(self):
            assert asyncio.get_running_loop() is self.loop
            calls.append("teardown")

    with case_context(Case) as case:
        loop = case.loop
        loop.run_until_complete(asyncio.sleep(0))
    assert calls == ["teardown"]
    assert loop.is_closed()


def test_child_case_cleans_up_failed_setup():
    calls = []

    class Case(unittest.TestCase):
        def setUp(self):
            self.addCleanup(calls.append, "cleanup")
            raise ValueError("setup")

        def tearDown(self):
            calls.append("unexpected teardown")

    with pytest.raises(ValueError, match="setup"):
        with case_context(Case):
            pytest.fail("body must not run")
    assert calls == ["cleanup"]


def test_child_case_rejects_disabled_assertions():
    result = run_process(
        sys.executable,
        "-O",
        "-c",
        "import unittest; from tests.process import case_context; "
        "context = case_context(unittest.TestCase); context.__enter__()",
    )
    result.check_returncode(1)
    assert b"require Python assertions to be enabled" in result.stderr


@pytest.mark.parametrize("optimization", ["1", "2"])
def test_child_helper_assertions_preserve_product_optimization(tmp_path, optimization):
    helper = tmp_path / "process_assertion_helper.py"
    helper.write_text(
        "import unittest\n"
        "class Case(unittest.TestCase):\n"
        "    def runTest(self):\n"
        "        assert False, 'helper assertion'\n"
    )
    (tmp_path / "optimized_product.py").write_text("debug = __debug__\n")
    result = run_process(
        sys.executable,
        "-c",
        f"""
import py_compile
import sys
import tests
from tests.process import case_context, enable_test_assertions
py_compile.compile({str(helper)!r}, optimize={optimization})
sys.path.insert(0, {str(tmp_path)!r})
tests.__path__.append({str(tmp_path)!r})
enable_test_assertions()
enable_test_assertions()
from tests.process_assertion_helper import Case
import optimized_product
if optimized_product.debug or not sys.flags.optimize:
    raise RuntimeError("product optimization was disabled")
with case_context(Case) as case:
    case.runTest()
""",
        env_overrides={"PYTHONOPTIMIZE": optimization},
    )
    result.check_returncode(1)
    assert b"AssertionError: helper assertion" in result.stderr
