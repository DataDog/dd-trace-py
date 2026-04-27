"""
Tests for pytest-xdist compatibility with the new test optimization plugin.

These tests run pytest in a **subprocess** with xdist enabled, using a local
mock HTTP server to capture the citestcycle payloads.  This ensures full
isolation from the outer pytest process and exercises the real multi-process
code path (main controller + N workers), each creating their own
SessionManager and writer.

The mock server responds to all API client endpoints (settings, known tests,
skippable tests, git search_commits) with sensible defaults and records every
POST to /api/v2/citestcycle so the test can inspect what events were actually
sent.
"""

from __future__ import annotations

import gzip
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
import typing as t

import msgpack
import pytest


# ---------------------------------------------------------------------------
# Mock HTTP server
# ---------------------------------------------------------------------------


class _MockCIVisibilityHandler(BaseHTTPRequestHandler):
    """HTTP request handler that mimics the Datadog CI Visibility backend.

    Records all citestcycle POSTs so tests can inspect them after the
    subprocess finishes.
    """

    # Class-level storage shared via the server instance.
    # Each test gets its own HTTPServer so there is no cross-test leakage.

    def log_message(self, format: str, *args: t.Any) -> None:  # noqa: A002
        # Silence request logs in test output.
        pass

    # -- helpers -------------------------------------------------------------

    def _send_json(self, data: t.Any, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        if self.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw

    # -- routing -------------------------------------------------------------

    def do_POST(self) -> None:
        body = self._read_body()

        if self.path == "/api/v2/citestcycle":
            payload = msgpack.unpackb(body)
            self.server.recorded_payloads.append(payload)  # type: ignore[attr-defined]
            self._send_json({})
            return

        if self.path == "/api/v2/libraries/tests/services/setting":
            self._send_json(
                {
                    "data": {
                        "id": "1",
                        "type": "ci_app_test_service_libraries_settings",
                        "attributes": {
                            "code_coverage": False,
                            "tests_skipping": False,
                            "itr_enabled": False,
                            "require_git": False,
                            "early_flake_detection": {
                                "enabled": False,
                            },
                            "flaky_test_retries_enabled": False,
                            "known_tests_enabled": False,
                            "test_management": {
                                "enabled": False,
                            },
                        },
                    }
                }
            )
            return

        if self.path == "/api/v2/ci/libraries/tests":
            self._send_json({"data": {"id": "1", "type": "ci_app_libraries_tests", "attributes": {"tests": {}}}})
            return

        if self.path == "/api/v2/ci/tests/skippable":
            self._send_json({"data": [], "meta": {}})
            return

        if self.path == "/api/v2/git/repository/search_commits":
            self._send_json({"data": []})
            return

        if self.path == "/api/v2/git/repository/packfile":
            self._send_json({})
            return

        if self.path == "/api/v2/citestcov":
            self._send_json({})
            return

        if self.path == "/api/v2/test/libraries/test-management/tests":
            self._send_json(
                {"data": {"id": "1", "type": "ci_app_libraries_tests_test_management", "attributes": {"tests": {}}}}
            )
            return

        # Fallback: accept but ignore.
        self._send_json({})

    def do_GET(self) -> None:
        # The EVP proxy path hits GET /info, but we use agentless mode so this
        # shouldn't be reached.  Respond defensively anyway.
        self._send_json({})

    def do_PUT(self) -> None:
        self._send_json({})


class MockCIVisibilityServer:
    """Context manager that starts and stops the mock server."""

    def __init__(self) -> None:
        self.server: t.Optional[HTTPServer] = None
        self.thread: t.Optional[threading.Thread] = None

    def __enter__(self) -> "MockCIVisibilityServer":
        self.server = HTTPServer(("127.0.0.1", 0), _MockCIVisibilityHandler)
        self.server.recorded_payloads = []  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc: t.Any) -> None:
        if self.server:
            self.server.shutdown()
        if self.thread:
            self.thread.join(timeout=5)

    @property
    def url(self) -> str:
        assert self.server is not None
        host, port = self.server.server_address
        hostname = host.decode() if isinstance(host, bytes) else host
        return f"http://{hostname}:{port}"

    @property
    def recorded_payloads(self) -> list[dict[str, t.Any]]:
        assert self.server is not None
        return self.server.recorded_payloads  # type: ignore[attr-defined]

    def get_all_events(self) -> list[dict[str, t.Any]]:
        """Return a flat list of all events across all recorded payloads."""
        events: list[dict[str, t.Any]] = []
        for payload in self.recorded_payloads:
            events.extend(payload.get("events", []))
        return events

    def get_events_by_type(self, event_type: str) -> list[dict[str, t.Any]]:
        return [e for e in self.get_all_events() if e.get("type") == event_type]

    def get_test_events(self) -> list[dict[str, t.Any]]:
        return self.get_events_by_type("test")

    def get_suite_events(self) -> list[dict[str, t.Any]]:
        return self.get_events_by_type("test_suite_end")

    def get_module_events(self) -> list[dict[str, t.Any]]:
        return self.get_events_by_type("test_module_end")

    def get_session_events(self) -> list[dict[str, t.Any]]:
        return self.get_events_by_type("test_session_end")

    def get_test_names(self) -> list[str]:
        return [e["content"]["meta"]["test.name"] for e in self.get_test_events()]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env(mock_server_url: str, extra: t.Optional[dict[str, str]] = None) -> dict[str, str]:
    """Build an environment dict for the subprocess that points at the mock server."""
    env = os.environ.copy()
    env.update(
        {
            "DD_API_KEY": "test-api-key-xdist",
            "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true",
            "DD_CIVISIBILITY_AGENTLESS_URL": mock_server_url,
            # Avoid interference from the outer test process.
            "DD_CIVISIBILITY_ENABLED": "true",
            # Set git info so the API client doesn't fail on missing tags.
            "DD_GIT_REPOSITORY_URL": "https://github.com/test/repo.git",
            "DD_GIT_COMMIT_SHA": "abc123",
            "DD_GIT_BRANCH": "main",
            # Use a stable service name.
            "DD_SERVICE": "xdist-test-service",
            "DD_ENV": "test",
        }
    )
    if extra:
        env.update(extra)
    return env


def _run_pytest_subprocess(
    test_dir: Path,
    *extra_args: str,
    env: dict[str, str],
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run pytest in a subprocess with the given environment."""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--ddtrace",
        "-v",
        "-s",
        str(test_dir),
        *extra_args,
    ]
    return subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(test_dir),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_server():
    """Provide a fresh mock CI Visibility server for each test."""
    with MockCIVisibilityServer() as server:
        yield server


@pytest.fixture
def test_project(tmp_path: Path) -> Path:
    """Create a minimal git repo with test files.

    Returns the project directory path.
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    # Initialize a git repo so the plugin doesn't complain.
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=project_dir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project_dir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project_dir, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=project_dir, capture_output=True)

    return project_dir


def _git_commit(project_dir: Path, message: str = "test commit") -> None:
    """Stage all files and create a commit."""
    subprocess.run(["git", "add", "."], cwd=project_dir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message, "--allow-empty", "--no-gpg-sign"],
        cwd=project_dir,
        capture_output=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# AIDEV-NOTE: These tests use subprocess to run pytest with xdist, pointing at
# a local mock HTTP server.  This is the only way to truly test multi-process
# xdist behavior since inline_run + EventCapture cannot cross process boundaries.


class TestXdistEventDelivery:
    """Verify that all expected events are delivered when running with xdist."""

    def test_all_tests_reported_with_two_workers(self, mock_server: MockCIVisibilityServer, test_project: Path) -> None:
        """All test events should arrive even when distributed across 2 workers."""
        (test_project / "test_a.py").write_text(
            textwrap.dedent("""\
                def test_one():
                    assert True

                def test_two():
                    assert True
            """)
        )
        (test_project / "test_b.py").write_text(
            textwrap.dedent("""\
                def test_three():
                    assert True

                def test_four():
                    assert True
            """)
        )
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        result = _run_pytest_subprocess(test_project, "-n", "2", env=env)

        assert result.returncode == 0, f"pytest failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        test_events = mock_server.get_test_events()
        test_names = sorted(e["content"]["meta"]["test.name"] for e in test_events)
        assert test_names == ["test_four", "test_one", "test_three", "test_two"], (
            f"Expected 4 test events, got {len(test_events)}: {test_names}"
        )

        # Verify we got session, module, and suite events.
        session_events = mock_server.get_session_events()
        assert len(session_events) == 1, f"Expected exactly 1 session event, got {len(session_events)}"

    def test_single_suite_across_workers(self, mock_server: MockCIVisibilityServer, test_project: Path) -> None:
        """When a single test file's tests are split across workers, all tests should still be reported."""
        # A single file with many tests — xdist may distribute them across workers.
        tests_code = "\n".join(f"def test_{i}():\n    assert True\n" for i in range(10))
        (test_project / "test_many.py").write_text(tests_code)
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        result = _run_pytest_subprocess(test_project, "-n", "3", env=env)

        assert result.returncode == 0, f"pytest failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        test_events = mock_server.get_test_events()
        test_names = sorted(e["content"]["meta"]["test.name"] for e in test_events)
        expected_names = sorted(f"test_{i}" for i in range(10))
        assert test_names == expected_names, f"Missing tests: expected {expected_names}, got {test_names}"

    def test_no_tests_lost_with_many_workers(self, mock_server: MockCIVisibilityServer, test_project: Path) -> None:
        """Stress test: many test files with many workers.  No events should be lost."""
        num_files = 5
        tests_per_file = 4
        for i in range(num_files):
            code = "\n".join(f"def test_f{i}_t{j}():\n    assert True\n" for j in range(tests_per_file))
            (test_project / f"test_file_{i}.py").write_text(code)
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        result = _run_pytest_subprocess(test_project, "-n", "4", env=env)

        assert result.returncode == 0, f"pytest failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        test_events = mock_server.get_test_events()
        expected_count = num_files * tests_per_file
        assert len(test_events) == expected_count, (
            f"Expected {expected_count} test events, got {len(test_events)}: "
            f"{sorted(e['content']['meta']['test.name'] for e in test_events)}"
        )

    def test_session_event_only_from_main_process(
        self, mock_server: MockCIVisibilityServer, test_project: Path
    ) -> None:
        """Exactly one session event should be sent (from the main process, not workers)."""
        (test_project / "test_simple.py").write_text("def test_ok():\n    assert True\n")
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        result = _run_pytest_subprocess(test_project, "-n", "2", env=env)

        assert result.returncode == 0, f"pytest failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        session_events = mock_server.get_session_events()
        assert len(session_events) == 1, (
            f"Expected exactly 1 session event (from main process), got {len(session_events)}"
        )

    def test_session_id_consistent_across_events(self, mock_server: MockCIVisibilityServer, test_project: Path) -> None:
        """All events should reference the same test_session_id."""
        (test_project / "test_a.py").write_text("def test_x():\n    assert True\n")
        (test_project / "test_b.py").write_text("def test_y():\n    assert True\n")
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        result = _run_pytest_subprocess(test_project, "-n", "2", env=env)

        assert result.returncode == 0, f"pytest failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        all_events = mock_server.get_all_events()
        session_ids = {e["content"].get("test_session_id") for e in all_events}

        assert len(session_ids) == 1, (
            f"Expected all events to share one test_session_id, got {len(session_ids)}: {session_ids}"
        )


class TestXdistSuiteAndModuleEvents:
    """Verify suite and module event correctness with xdist."""

    def test_suite_events_match_test_files(self, mock_server: MockCIVisibilityServer, test_project: Path) -> None:
        """Each test file should produce at least one suite event."""
        (test_project / "test_alpha.py").write_text("def test_a1():\n    assert True\n")
        (test_project / "test_beta.py").write_text("def test_b1():\n    assert True\n")
        (test_project / "test_gamma.py").write_text("def test_g1():\n    assert True\n")
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        result = _run_pytest_subprocess(test_project, "-n", "2", env=env)

        assert result.returncode == 0, f"pytest failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        suite_events = mock_server.get_suite_events()
        suite_names = sorted(e["content"]["meta"]["test.suite"] for e in suite_events)

        # Each file should appear at least once.
        for expected_suite in ["test_alpha.py", "test_beta.py", "test_gamma.py"]:
            assert expected_suite in suite_names, f"Missing suite event for {expected_suite}, got: {suite_names}"

    def test_test_suite_id_references_valid_suite(
        self, mock_server: MockCIVisibilityServer, test_project: Path
    ) -> None:
        """Every test event's test_suite_id should match a suite event's test_suite_id."""
        (test_project / "test_ref.py").write_text(
            textwrap.dedent("""\
                def test_r1():
                    assert True
                def test_r2():
                    assert True
            """)
        )
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        result = _run_pytest_subprocess(test_project, "-n", "2", env=env)

        assert result.returncode == 0, f"pytest failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        suite_ids = {e["content"]["test_suite_id"] for e in mock_server.get_suite_events()}
        for test_event in mock_server.get_test_events():
            test_suite_id = test_event["content"]["test_suite_id"]
            assert test_suite_id in suite_ids, (
                f"Test {test_event['content']['meta']['test.name']} references test_suite_id "
                f"{test_suite_id} which has no matching suite event. Known suite IDs: {suite_ids}"
            )


class TestXdistWithFailures:
    """Verify event delivery when some tests fail."""

    def test_failed_tests_still_reported(self, mock_server: MockCIVisibilityServer, test_project: Path) -> None:
        """Failed tests should produce events with status=fail."""
        (test_project / "test_mixed.py").write_text(
            textwrap.dedent("""\
                def test_pass():
                    assert True

                def test_fail():
                    assert False
            """)
        )
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        result = _run_pytest_subprocess(test_project, "-n", "2", env=env)

        # pytest should report failure
        assert result.returncode != 0

        test_events = mock_server.get_test_events()
        test_names = sorted(e["content"]["meta"]["test.name"] for e in test_events)
        assert "test_pass" in test_names, f"Missing test_pass event; got: {test_names}"
        assert "test_fail" in test_names, f"Missing test_fail event; got: {test_names}"

        # Check statuses
        statuses = {e["content"]["meta"]["test.name"]: e["content"]["meta"]["test.status"] for e in test_events}
        assert statuses["test_pass"] == "pass"
        assert statuses["test_fail"] == "fail"

    def test_session_status_fail_on_test_failure(self, mock_server: MockCIVisibilityServer, test_project: Path) -> None:
        """Session event should have status=fail when any test fails."""
        (test_project / "test_fail.py").write_text("def test_boom():\n    assert False\n")
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        result = _run_pytest_subprocess(test_project, "-n", "1", env=env)

        assert result.returncode != 0

        session_events = mock_server.get_session_events()
        assert len(session_events) == 1
        assert session_events[0]["content"]["meta"]["test.status"] == "fail"


class TestXdistWithoutPlugin:
    """Verify behavior when --ddtrace is not passed (baseline)."""

    def test_no_events_without_ddtrace_flag(self, mock_server: MockCIVisibilityServer, test_project: Path) -> None:
        """Without --ddtrace, no events should be sent to the mock server."""
        (test_project / "test_noop.py").write_text("def test_ok():\n    assert True\n")
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        # Disable the plugin so it doesn't auto-activate via DD_PYTEST_USE_NEW_PLUGIN.
        env["DD_CIVISIBILITY_ENABLED"] = "false"
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "-v",
            "-s",
            str(test_project),
            "-n",
            "2",
        ]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60, cwd=str(test_project))

        assert result.returncode == 0
        assert len(mock_server.get_all_events()) == 0, "No events should be sent when plugin is disabled"


class TestXdistWorkerCrashRestart:
    """Verify event delivery when workers crash and xdist restarts them.

    When a worker crashes (e.g., os._exit, SIGKILL, segfault):
    - The daemon writer thread dies immediately (no final flush)
    - atexit handlers do NOT run for os._exit / SIGKILL
    - pytest_sessionfinish does NOT run on the crashed worker
    - Any buffered events that weren't flushed in the last 60s interval are lost

    pytest-xdist's --max-worker-restart controls how many restarts are allowed.
    The restarted worker picks up remaining tests but is a fresh process with
    a new SessionManager and writer — it knows nothing about the old worker.
    """

    def test_crash_with_restart_healthy_worker_unaffected(
        self, mock_server: MockCIVisibilityServer, test_project: Path
    ) -> None:
        """Tests on a separate healthy worker should be reported when another crashes.

        With --dist=loadscope, each file runs on a single worker. This
        guarantees test_healthy.py runs entirely on a different worker from
        the crash file. Without loadscope, random scheduling could colocate
        a healthy test with the crash, losing it too (as documented in
        test_crash_loses_buffered_events_on_same_worker).
        """
        (test_project / "test_crash.py").write_text(
            textwrap.dedent("""\
                import os

                def test_will_crash():
                    '''This test kills its own worker process.'''
                    os._exit(1)
            """)
        )
        (test_project / "test_healthy.py").write_text(
            textwrap.dedent("""\
                def test_healthy_one():
                    assert True

                def test_healthy_two():
                    assert True
            """)
        )
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        # Use --dist=loadscope to guarantee file-level isolation between workers.
        result = _run_pytest_subprocess(
            test_project, "-n", "2", "--max-worker-restart", "4", "--dist=loadscope", env=env
        )

        test_events = mock_server.get_test_events()
        test_names = sorted(e["content"]["meta"]["test.name"] for e in test_events)

        # The healthy tests ran on a different worker and should be reported.
        assert "test_healthy_one" in test_names, (
            f"Healthy test events missing. Got: {test_names}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "test_healthy_two" in test_names, (
            f"Healthy test events missing. Got: {test_names}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        # The crashing test's event is lost because the worker died before
        # the writer could flush.
        assert "test_will_crash" not in test_names, (
            "Crashing test event was unexpectedly delivered — "
            "this is good! Update this test to assert presence instead."
        )

    def test_crash_loses_buffered_events_on_same_worker(
        self, mock_server: MockCIVisibilityServer, test_project: Path
    ) -> None:
        """Demonstrate that a worker crash loses ALL buffered events on that worker.

        The writer has a 60-second flush interval and uses a daemon thread.
        When os._exit(1) kills the worker, the daemon writer thread dies without
        flushing. ALL events buffered on that worker (not just the crashing test)
        are lost. This includes tests that PASSED before the crash on the same
        worker.

        This test documents the current behavior — it is a known limitation.
        AIDEV-NOTE: If the writer is changed to flush after each test, or to
        use a non-daemon thread with proper shutdown, this test should be updated.
        """
        # Use -n 1 so there is only one worker. Put a passing test and a
        # crashing test in the same file (guaranteeing same worker). Disable
        # random ordering with -p no:randomly to ensure deterministic execution.
        (test_project / "test_crash_sequence.py").write_text(
            textwrap.dedent("""\
                import os

                def test_passes_then_crash_kills_it():
                    '''This passes but its event will be lost when the next test crashes.'''
                    assert True

                def test_crashes_worker():
                    '''This crashes the worker via os._exit.'''
                    os._exit(1)
            """)
        )
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        _run_pytest_subprocess(test_project, "-n", "1", "--max-worker-restart", "1", "-p", "no:randomly", env=env)

        test_events = mock_server.get_test_events()
        test_names = [e["content"]["meta"]["test.name"] for e in test_events]

        # Both events are lost: the crashing test AND the passing test that ran
        # before it on the same worker. The 60-second flush didn't fire in time.
        assert "test_crashes_worker" not in test_names, (
            "Crashing test event was unexpectedly delivered — "
            "this is good! Update this test to assert presence instead."
        )
        assert "test_passes_then_crash_kills_it" not in test_names, (
            "Passing test's event was unexpectedly delivered despite being buffered "
            "on the same worker that crashed. If the writer now flushes eagerly, "
            "update this test to expect its presence."
        )

    def test_multiple_crashes_with_max_restart(self, mock_server: MockCIVisibilityServer, test_project: Path) -> None:
        """Multiple worker crashes with --max-worker-restart=4.

        Each crash kills the worker and loses ALL buffered events on that worker.
        This test demonstrates that crashes can cause cascading data loss: a
        crash kills not just the crashing test's event but also any previously
        passed tests whose events were buffered on the same worker.

        With random scheduling, a worker may run several passing tests before
        hitting a crash test, losing all of them.
        """
        # Create crash files and healthy files.
        for i in range(3):
            (test_project / f"test_crash_{i}.py").write_text(
                textwrap.dedent(f"""\
                    import os

                    def test_crash_{i}():
                        os._exit(1)
                """)
            )
        for i in range(3):
            (test_project / f"test_ok_{i}.py").write_text(
                textwrap.dedent(f"""\
                    def test_ok_{i}():
                        assert True
                """)
            )
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        _run_pytest_subprocess(test_project, "-n", "2", "--max-worker-restart", "4", env=env)

        test_events = mock_server.get_test_events()
        test_names = sorted(e["content"]["meta"]["test.name"] for e in test_events)
        crash_tests = [n for n in test_names if n.startswith("test_crash_")]

        # Crashing tests should never appear (their worker died before flush).
        assert len(crash_tests) == 0, f"Crash test events should be lost, but got: {crash_tests}"

        # Some or all healthy tests may be lost too if they shared a worker
        # with a crash test (their events were buffered but not flushed).
        # AIDEV-NOTE: This documents real data loss. The number of surviving
        # ok tests depends on scheduling luck. We only assert the session
        # event (from the main process) is always present.
        session_events = mock_server.get_session_events()
        assert len(session_events) == 1, (
            f"Session event from main process should always be present, got {len(session_events)}"
        )

    def test_no_restart_loses_remaining_tests(self, mock_server: MockCIVisibilityServer, test_project: Path) -> None:
        """With --max-worker-restart=0, a crash kills the worker permanently.

        Tests assigned to that worker that haven't run yet are never executed.
        Only tests from the surviving worker(s) are reported.
        """
        # Put the crash in one file, healthy tests in another.
        # With -n 1, the single worker crashes and no restart happens.
        (test_project / "test_crash_first.py").write_text(
            textwrap.dedent("""\
                import os

                def test_boom():
                    os._exit(1)

                def test_never_runs():
                    assert True
            """)
        )
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        _run_pytest_subprocess(test_project, "-n", "1", "--max-worker-restart", "0", env=env)

        test_events = mock_server.get_test_events()
        test_names = [e["content"]["meta"]["test.name"] for e in test_events]

        # Neither test should be reported: the crashing test's events are lost,
        # and test_never_runs was never executed because the worker died.
        assert "test_never_runs" not in test_names, (
            f"test_never_runs was reported but shouldn't have run. Got: {test_names}"
        )

        # Session event should still be present (sent by main process).
        session_events = mock_server.get_session_events()
        assert len(session_events) == 1, f"Expected 1 session event from main process, got {len(session_events)}"

    def test_crash_session_id_consistency(self, mock_server: MockCIVisibilityServer, test_project: Path) -> None:
        """After worker restart, events from surviving/restarted workers use the same session_id.

        XdistTestOptPlugin.pytest_configure_node passes dd_session_id to each
        worker via workerinput. A restarted worker should receive the same
        session_id since configure_node runs again for the new worker.
        """
        # One file crashes, the other is healthy. Both workers produce events.
        # After restart, the restarted worker may pick up remaining work.
        (test_project / "test_crash_sid.py").write_text(
            textwrap.dedent("""\
                import os

                def test_crash_sid():
                    os._exit(1)
            """)
        )
        # Several healthy tests to ensure some run on the restarted worker.
        for i in range(4):
            (test_project / f"test_healthy_sid_{i}.py").write_text(
                textwrap.dedent(f"""\
                    def test_h{i}():
                        assert True
                """)
            )
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        _run_pytest_subprocess(test_project, "-n", "2", "--max-worker-restart", "2", env=env)

        all_events = mock_server.get_all_events()
        session_ids = {e["content"].get("test_session_id") for e in all_events if "content" in e}
        session_ids.discard(None)

        assert len(session_ids) == 1, (
            f"Events from restarted workers should share the same session_id. "
            f"Got {len(session_ids)} distinct IDs: {session_ids}"
        )


class TestXdistPartialFlush:
    """Verify that DD_TRACE_PARTIAL_FLUSH_MIN_SPANS mitigates crash data loss.

    These tests prove the workaround works by running the same crash scenario
    with and without the env var and comparing the outcomes.
    """

    def test_partial_flush_before_and_after(self, test_project: Path) -> None:
        """Same crash scenario, two runs: without and with DD_TRACE_PARTIAL_FLUSH_MIN_SPANS=1.

        Run 1 (no env var): the passing test's event is LOST because it was
        buffered on the same worker that crashed.

        Run 2 (env var set): the passing test's event is SAVED because the
        writer flushed it synchronously before the next test could crash.

        This is the definitive proof that the workaround fixes the data-loss
        issue — same test code, same crash, different outcome.
        """
        test_code = textwrap.dedent("""\
            import os

            def test_passes_before_crash():
                assert True

            def test_crashes_worker():
                os._exit(1)
        """)

        # --- Run 1: WITHOUT eager flushing (default) ---
        with MockCIVisibilityServer() as server_without:
            (test_project / "test_crash_sequence.py").write_text(test_code)
            _git_commit(test_project)

            env = _make_env(server_without.url)
            _run_pytest_subprocess(test_project, "-n", "1", "--max-worker-restart", "1", "-p", "no:randomly", env=env)

            names_without = [e["content"]["meta"]["test.name"] for e in server_without.get_test_events()]

        # --- Run 2: WITH eager flushing ---
        with MockCIVisibilityServer() as server_with:
            # Rewrite the file to reset git state for a clean commit.
            (test_project / "test_crash_sequence.py").write_text(test_code)
            _git_commit(test_project, message="re-commit for second run")

            env = _make_env(server_with.url, extra={"DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "1"})
            _run_pytest_subprocess(test_project, "-n", "1", "--max-worker-restart", "1", "-p", "no:randomly", env=env)

            names_with = [e["content"]["meta"]["test.name"] for e in server_with.get_test_events()]

        # --- Assertions: the contrast ---
        # Without eager flushing: the passing test is lost.
        assert "test_passes_before_crash" not in names_without, (
            f"Without eager flushing, the event should be lost. Got: {names_without}"
        )

        # With eager flushing: the passing test is saved.
        assert "test_passes_before_crash" in names_with, (
            f"With DD_TRACE_PARTIAL_FLUSH_MIN_SPANS=1, the event should be saved. Got: {names_with}"
        )

    def test_partial_flush_preserves_all_healthy_tests_with_crashes(
        self, mock_server: MockCIVisibilityServer, test_project: Path
    ) -> None:
        """With eager flushing, healthy tests on crash-affected workers are preserved."""
        for i in range(3):
            (test_project / f"test_crash_{i}.py").write_text(
                textwrap.dedent(f"""\
                    import os

                    def test_crash_{i}():
                        os._exit(1)
                """)
            )
        for i in range(5):
            (test_project / f"test_ok_{i}.py").write_text(
                textwrap.dedent(f"""\
                    def test_ok_{i}():
                        assert True
                """)
            )
        _git_commit(test_project)

        env = _make_env(mock_server.url, extra={"DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "1"})
        result = _run_pytest_subprocess(test_project, "-n", "2", "--max-worker-restart", "4", env=env)

        test_events = mock_server.get_test_events()
        test_names = sorted(e["content"]["meta"]["test.name"] for e in test_events)
        ok_tests = sorted(n for n in test_names if n.startswith("test_ok_"))

        # All 5 healthy tests should be present — eager flushing saved them
        # even if they shared a worker with crash tests.
        expected_ok = sorted(f"test_ok_{i}" for i in range(5))
        assert ok_tests == expected_ok, (
            f"Expected all healthy tests to be preserved with eager flushing.\n"
            f"Expected: {expected_ok}\nGot: {ok_tests}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


class TestXdistLoadScope:
    """Tests with --dist=loadscope to verify suite-level distribution."""

    def test_loadscope_all_tests_reported(self, mock_server: MockCIVisibilityServer, test_project: Path) -> None:
        """With --dist=loadscope, all tests in the same file run on the same worker."""
        (test_project / "test_scope_a.py").write_text(
            textwrap.dedent("""\
                def test_s1():
                    assert True
                def test_s2():
                    assert True
            """)
        )
        (test_project / "test_scope_b.py").write_text(
            textwrap.dedent("""\
                def test_s3():
                    assert True
                def test_s4():
                    assert True
            """)
        )
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        result = _run_pytest_subprocess(test_project, "-n", "2", "--dist=loadscope", env=env)

        assert result.returncode == 0, f"pytest failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        test_events = mock_server.get_test_events()
        test_names = sorted(e["content"]["meta"]["test.name"] for e in test_events)
        assert test_names == ["test_s1", "test_s2", "test_s3", "test_s4"]
