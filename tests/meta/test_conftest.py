import os
from pathlib import Path
import signal
import subprocess
import sys
from unittest import mock

import pytest

from tests import utils
from tests.utils import TracerTestCase
from tests.utils import override_env
from tests.utils import terminate_process_group


@pytest.mark.parametrize("mode", ["graceful", "stubborn", "child"])
def test_terminate_process_group(mode):
    code = """
import signal
import subprocess
import sys
import time

if sys.argv[1] == "child":
    subprocess.Popen([sys.executable, "-u", "-c", (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(60)"
    )])
else:
    if sys.argv[1] == "stubborn":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print("ready", flush=True)
time.sleep(60)
"""
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", code, mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert proc.stdout.readline() == b"ready\n"
        terminate_process_group(proc, timeout=0.2)
        assert proc.returncode in (-signal.SIGTERM, -signal.SIGKILL)
        assert proc.stdout.closed
    finally:
        terminate_process_group(proc, timeout=1)


class ConftestTestCase(TracerTestCase):
    """
    Test case to verify conftest code works as expected
    """

    @pytest.fixture(autouse=True)
    def fixtures(self, testdir):
        """
        Fixtures to use in tests
        """
        self.testdir = testdir

    def makeconftest(self, conftest_rel_path="../conftest.py"):
        """
        Copy tests/conftest.py contents to the testdir conftest
        """
        this_dir = os.path.dirname(__file__)
        abs_file_path = os.path.join(this_dir, conftest_rel_path)
        with open(abs_file_path, encoding="utf-8") as conftest:
            self.testdir.makeconftest(conftest.read())

    def inline_run(self, *args):
        """
        Override inline_run to use our conftest
        """
        with override_env({"DD_API_KEY": "foobar.baz"}):
            self.makeconftest()
            return self.testdir.inline_run(*args)

    def test_subprocess_fail(self):
        """
        Test a failing test with subprocess decorator
        """
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.subprocess()
            def test_fail():
                assert False
            """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run(file_name)
        rec.assertoutcome(passed=0, failed=1, skipped=0)

    def test_subprocess_pass(self):
        """
        Test a passing test with subprocess decorator
        """
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.subprocess()
            def test_pass():
                assert True
            """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run(file_name)
        rec.assertoutcome(passed=1, failed=0, skipped=0)

    def test_subprocess_skip(self):
        """
        Test a skipping test with subprocess decorator
        """
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.skip
            @pytest.mark.subprocess()
            def test_pass():
                assert True
            """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run(file_name)
        rec.assertoutcome(passed=0, failed=0, skipped=1)


@pytest.mark.parametrize("optimization", ["0", "1", "2"])
def test_subprocess_assertions_with_optimization(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, optimization: str
) -> None:
    root = Path(__file__).resolve().parents[2]
    pytester.makeconftest((root / "tests/conftest.py").read_text())
    pytester.makepyfile(application="def optimized():\n    return not __debug__\n")
    pytester.makepyfile(
        test_assertions="""
        import pytest

        @pytest.mark.subprocess(timeout=10)
        def test_failing_assertion():
            assert False, "subprocess assertion must execute"

        @pytest.mark.subprocess(timeout=10)
        def test_application_optimization():
            import os
            import sys
            import application

            expected = int(os.environ["PYTHONOPTIMIZE"])
            assert sys.flags.optimize == expected
            assert application.optimized() is bool(expected)
        """
    )
    monkeypatch.setenv("PYTHONOPTIMIZE", optimization)
    monkeypatch.setenv("DD_API_KEY", "foobar.baz")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(pytester.path), str(root), os.getenv("PYTHONPATH", "")]))
    result = pytester.runpytest_subprocess("--no-cov", "-o", "addopts=", "test_assertions.py", timeout=30)
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*subprocess assertion must execute*"])


@pytest.mark.parametrize("inherited_token", [None, "parent-snapshot"])
def test_session_token_survives_writer_replacement(pytester, monkeypatch, inherited_token):
    root = Path(__file__).resolve().parents[2]
    pytester.makeconftest((root / "tests/conftest.py").read_text())
    pytester.makepyfile(
        test_token="""
        import os
        import ddtrace
        from ddtrace.internal.utils.formats import parse_tags_str

        headers = parse_tags_str(os.environ["_DD_TRACE_WRITER_ADDITIONAL_HEADERS"])
        token = headers["X-Datadog-Test-Session-Token"]
        assert token
        assert headers["Other"] == "preserved"
        assert ddtrace.tracer._span_aggregator.writer._test_session_token == token
        if os.environ["EXPECTED_SNAPSHOT_TOKEN"]:
            assert token == os.environ["EXPECTED_SNAPSHOT_TOKEN"]

        def test_replaced_writer(tracer):
            assert tracer._span_aggregator.writer._test_session_token == token
        """
    )
    headers = "Other:preserved"
    if inherited_token is not None:
        headers += ",X-Datadog-Test-Session-Token:" + inherited_token
    monkeypatch.setenv("_DD_TRACE_WRITER_ADDITIONAL_HEADERS", headers)
    monkeypatch.setenv("EXPECTED_SNAPSHOT_TOKEN", inherited_token or "")
    monkeypatch.setenv("DD_API_KEY", "foobar.baz")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(root), os.getenv("PYTHONPATH", "")]))
    result = pytester.runpytest_subprocess("--no-cov", "-o", "addopts=", "test_token.py", timeout=30)
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize(
    "body, outcome",
    [(b"Received unmatched traces", pytest.xfail.Exception), (b"Different span attributes", pytest.fail.Exception)],
)
def test_snapshot_comparison_outcomes(monkeypatch, body, outcome):
    session = utils.SnapshotTest("attempt", name="baseline", base_url="http://localhost:9126")
    monkeypatch.setattr(session._client, "_request", lambda *args, **kwargs: (400, body))
    with pytest.raises(outcome, match=body.decode()):
        session.verify([], timeout=1)


def test_testagent_connection_failure(monkeypatch):
    client = utils.TestAgentClient("http://localhost:9126", token="attempt")
    connection = mock.Mock()
    connection.request.side_effect = ConnectionRefusedError("agent unavailable")
    monkeypatch.setattr(client, "create_connection", lambda **kwargs: connection)
    with pytest.raises(pytest.xfail.Exception, match="Failed to connect to test agent"):
        client._request("GET", client._url("/test/session/requests"), retry=False)
    connection.close.assert_called_once()
