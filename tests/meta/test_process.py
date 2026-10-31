import os
import subprocess
import sys
from textwrap import dedent

import pytest

from tests.process import child_environment
from tests.process import run_process


def test_runner_import_does_not_initialize_ddtrace():
    result = run_process(sys.executable, "-c", "import tests.process, sys; assert 'ddtrace' not in sys.modules")
    result.check_returncode()


def test_environment_inheritance_and_replacement(monkeypatch):
    monkeypatch.setenv("PROCESS_PARENT", "parent")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--ddtrace")
    assert child_environment({}) == {}
    assert child_environment({"CHILD": "child", "UNSET": None}) == {"CHILD": "child"}
    env = child_environment()
    assert "PYTEST_ADDOPTS" not in env
    assert env["PROCESS_PARENT"] == "parent"
    assert os.environ["PROCESS_PARENT"] == "parent"


@pytest.mark.parametrize("ddtrace_run", [False, True])
def test_result_unpacking(python_process, ddtrace_run):
    result = python_process(
        "import os, sys; print(os.getpid()); sys.stderr.write('failure'); sys.exit(7)",
        ddtrace_run=ddtrace_run,
    )
    out, err, status, pid = result
    assert int(out) == pid
    assert pid != os.getpid()
    assert err == b"failure"
    assert status == 7
    assert isinstance(result, tuple)
    result.check_returncode(7)


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


def test_child_helper_assertions_preserve_product_optimization(tmp_path):
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
import sys
import tests
from tests.process import run_scenario
sys.path.insert(0, {str(tmp_path)!r})
tests.__path__.append({str(tmp_path)!r})
import optimized_product
if optimized_product.debug or not sys.flags.optimize:
    raise RuntimeError("product optimization was disabled")
run_scenario("tests.process_assertion_helper", "Case", "runTest")
""",
        env={**os.environ, "PYTHONOPTIMIZE": "1"},
    )
    result.check_returncode(1)
    assert b"AssertionError: helper assertion" in result.stderr


def test_scenario_preserves_output_and_isolation(python_process, tmp_path):
    (tmp_path / "scenario.py").write_text(
        dedent("""
        import logging
        import sys
        import unittest

        assert "ddtrace" not in sys.modules

        class Case(unittest.TestCase):
            def test_body(self):
                print("scenario output")
                logging.warning("scenario log")
        """)
    )
    result = python_process(
        f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
        "from tests.process import run_scenario; run_scenario('scenario', 'Case', 'test_body')"
    )
    result.check_returncode()
    assert result.stdout == b"scenario output\n"
    assert result.stderr == b"WARNING:root:scenario log\n"


@pytest.mark.parametrize(
    "body, teardown, status, message",
    [
        ("pytest.skip('unavailable')", "pass", 77, "unavailable"),
        ("pytest.xfail('known issue')", "pass", 78, "known issue"),
        ("pytest.skip('unavailable')", "raise ValueError('cleanup failed')", 1, "cleanup failed"),
    ],
)
def test_scenario_outcomes_after_cleanup(python_process, tmp_path, body, teardown, status, message):
    cleaned = tmp_path / "cleaned"
    (tmp_path / "scenario.py").write_text(
        dedent(f"""
        from pathlib import Path
        import unittest
        import pytest

        class Case(unittest.TestCase):
            def setUp(self):
                self.addCleanup(Path({str(cleaned)!r}).touch)

            def tearDown(self):
                {teardown}

            def test_body(self):
                {body}
    """)
    )
    result = python_process(
        f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
        "from tests.process import run_scenario; run_scenario('scenario', 'Case', 'test_body')"
    )
    result.check_returncode(status)
    assert message.encode() in result.stdout + result.stderr
    assert cleaned.exists()


def test_async_unittest_requires_async_case(python_process, tmp_path):
    (tmp_path / "scenario.py").write_text(
        dedent("""
        import unittest
        class Case(unittest.TestCase):
            async def test_body(self):
                raise AssertionError("must not silently pass an unawaited method")
    """)
    )
    result = python_process(
        f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
        "from tests.process import run_scenario; run_scenario('scenario', 'Case', 'test_body')"
    )
    result.check_returncode(1)
    assert b"Async unittest methods must inherit from unittest.IsolatedAsyncioTestCase" in result.stderr
