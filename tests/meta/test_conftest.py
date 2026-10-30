import os
from pathlib import Path
import signal
import subprocess
import sys
from unittest import mock

import pytest

from tests import utils
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


@pytest.fixture
def pytester(pytester, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    pytester.makeconftest((root / "tests/conftest.py").read_text())
    monkeypatch.setenv("DD_API_KEY", "foobar.baz")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(pytester.path), str(root), os.getenv("PYTHONPATH", "")]))
    return pytester


def test_process_fixture_is_unskippable(pytester):
    pytester.makepyfile(
        """
        def test_selection(request, python_process):
            reasons = [m.kwargs.get("reason") for m in request.node.iter_markers("skipif")]
            assert "datadog_itr_unskippable" in reasons
        """
    )
    pytester.runpytest_subprocess("--no-cov", "-o", "addopts=").assert_outcomes(passed=1)


def test_subprocess_skips(pytester):
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.skip
        @pytest.mark.subprocess
        def test_parent_skip():
            raise AssertionError("skipped method ran")

        @pytest.mark.subprocess
        def test_child_skip():
            import unittest
            raise unittest.SkipTest("child platform")
        """
    )
    pytester.runpytest_subprocess("--no-cov", "-o", "addopts=").assert_outcomes(skipped=2)


@pytest.mark.parametrize("optimization", ["0", "1", "2"])
def test_subprocess_assertions_with_optimization(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, optimization: str
) -> None:
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
    result = pytester.runpytest_subprocess("--no-cov", "-o", "addopts=", "test_assertions.py", timeout=30)
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["*subprocess assertion must execute*"])


def test_class_subprocess(pytester, monkeypatch):
    pytester.makeconftest(
        (pytester.path / "conftest.py").read_text() + "\n@pytest.fixture(autouse=True)\ndef unrelated_fixture():\n"
        "    raise AssertionError('isolated method loaded a parent fixture')\n"
    )
    pytester.makepyfile(
        test_cases="""
        import asyncio
        import os
        from pathlib import Path
        import unittest
        import pytest

        MODE = os.getenv("CHILD_MODE", "parent")

        class TestCase(unittest.IsolatedAsyncioTestCase):
            async def asyncSetUp(self):
                assert MODE == "child"
                assert os.getpid() != int(os.environ["PARENT_PID"])
                self.loop = asyncio.get_running_loop()

            async def asyncTearDown(self):
                assert asyncio.get_running_loop() is self.loop
                with Path(os.environ["EVENTS"]).open("a") as stream:
                    stream.write(self._testMethodName + "\\n")

            @pytest.mark.subprocess(env={"CHILD_MODE": "child"}, parametrize={"CASE": ["one", "two"]})
            async def test_async(self):
                await asyncio.sleep(0)
                assert asyncio.get_running_loop() is self.loop
                assert os.environ["CASE"] in ("one", "two")

            @pytest.mark.subprocess(env={"CHILD_MODE": "child", "PYTHONOPTIMIZE": "1"})
            def test_failure(self):
                assert False, "class assertion must execute"
        """
    )
    monkeypatch.setenv("PARENT_PID", str(os.getpid()))
    monkeypatch.setenv("EVENTS", str(pytester.path / "events"))
    result = pytester.runpytest_subprocess("--no-cov", "-o", "addopts=", "-v", "test_cases.py", timeout=30)
    result.assert_outcomes(passed=2, failed=1)
    result.stdout.fnmatch_lines(["*class assertion must execute*"])
    for variant in ("one", "two"):
        result.stdout.re_match_lines([rf".*TestCase::test_async\[{variant}\].*PASSED.*"])
    assert sorted((pytester.path / "events").read_text().splitlines()) == ["test_async", "test_async", "test_failure"]


def test_class_subprocess_preserves_collection_and_explicit_fixtures(pytester, monkeypatch):
    pytester.makeconftest(
        (pytester.path / "conftest.py").read_text()
        + """
@pytest.fixture(autouse=True)
def unrelated_fixture():
    raise AssertionError("isolated method loaded a parent fixture")

@pytest.fixture
def parent_environment(monkeypatch):
    monkeypatch.setenv("EXPLICIT_FIXTURE", "configured")

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    setattr(item, "rep_" + call.when, outcome.get_result())

@contextlib.contextmanager
def _snapshot_context(token, **kwargs):
    with open(os.environ["EVENTS"], "a") as stream:
        stream.write("enter:" + token + "\\n")
    yield mock.Mock()
    with open(os.environ["EVENTS"], "a") as stream:
        stream.write("exit:" + token + "\\n")
"""
    )
    pytester.makepyfile(
        test_metadata="""
        import os
        import unittest
        import pytest

        @pytest.fixture
        def explicit_fixture(request, parent_environment):
            assert isinstance(request.instance, request.cls)
            assert len(list(request.node.iter_markers("usefixtures"))) == 1

        @pytest.mark.subprocess
        @pytest.mark.usefixtures("explicit_fixture")
        class Base:
            def setup_method(self, method):
                self.ready = True

            @pytest.mark.snapshot
            def test_inherited(self):
                assert self.ready
                assert os.environ["EXPLICIT_FIXTURE"] == "configured"

        class TestUnit(Base, unittest.TestCase):
            pass

        class TestPlain(Base):
            pass
        """
    )
    monkeypatch.setenv("EVENTS", str(pytester.path / "events"))
    result = pytester.runpytest_subprocess("--no-cov", "-o", "addopts=", "test_metadata.py", timeout=30)
    result.assert_outcomes(passed=2)
    events = (pytester.path / "events").read_text().splitlines()
    for cls in ("TestUnit", "TestPlain"):
        prefix = f"test_metadata.{cls}.test_inherited"
        entered = [event.removeprefix("enter:") for event in events if event.startswith("enter:" + prefix)]
        assert len(entered) == 1
        assert events.count("exit:" + entered[0]) == 1


@pytest.mark.parametrize("inherited_token", [None, "parent-snapshot"])
def test_session_token_survives_writer_replacement(pytester, monkeypatch, inherited_token):
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
