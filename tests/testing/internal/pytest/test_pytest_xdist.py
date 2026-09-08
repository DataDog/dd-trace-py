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
import tempfile
import textwrap
import threading
import typing as t
from unittest import mock

import msgpack
import pytest

from ddtrace.testing.internal.constants import DD_TEST_OPTIMIZATION_ENV_DATA_FILE
from ddtrace.testing.internal.constants import DD_TEST_OPTIMIZATION_MANIFEST_FILE
from ddtrace.testing.internal.constants import DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES
from ddtrace.testing.internal.constants import TEST_UNDECLARED_OUTPUTS_DIR
from ddtrace.testing.internal.constants import XDIST_MANIFEST_DIR_PREFIX
import ddtrace.testing.internal.pytest.xdist as xdist_module
from ddtrace.testing.internal.pytest.xdist import generate_xdist_manifest
from ddtrace.testing.internal.pytest.xdist import resolve_inherited_manifest_env


# ---------------------------------------------------------------------------
# Mock HTTP server
# ---------------------------------------------------------------------------


def _settings_attributes() -> dict[str, t.Any]:
    """The ``attributes`` block returned by the settings endpoint.

    This must be complete enough for ``Settings.from_attributes`` to parse without raising. In particular
    ``early_flake_detection`` requires ``slow_test_retries`` and ``faulty_session_threshold``, and ``test_management``
    requires ``attempt_to_fix_retries`` — accessed with ``[]`` (not ``.get()``) in settings_data.py. If any are
    missing, ``APIClient.get_settings`` swallows the ``KeyError`` and silently returns a default ``Settings()`` with
    *every* feature disabled, which would make these tests pass against fallback defaults rather than the configured
    values. Kept in one place so ``test_mock_settings_payload_is_parseable`` can guard it.
    """
    return {
        "code_coverage": False,
        "tests_skipping": False,
        "itr_enabled": False,
        "require_git": False,
        "early_flake_detection": {
            "enabled": False,
            "slow_test_retries": {"5s": 10, "10s": 5, "30s": 3, "5m": 2},
            "faulty_session_threshold": 30,
        },
        "flaky_test_retries_enabled": False,
        "known_tests_enabled": False,
        "test_management": {
            "enabled": False,
            "attempt_to_fix_retries": 20,
        },
    }


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

        self.server.recorded_request_paths.append(self.path)  # type: ignore[attr-defined]

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
                        "attributes": self.server.settings_attributes,  # type: ignore[attr-defined]
                    }
                }
            )
            return

        if self.path == "/api/v2/ci/libraries/tests":
            self._send_json({"data": {"id": "1", "type": "ci_app_libraries_tests", "attributes": {"tests": {}}}})
            return

        if self.path == "/api/v2/ci/tests/skippable":
            # NOTE: meta.correlation_id is required. Without it the API client records a configuration error, which
            # (among other things) makes the controller decline to cache its data for the xdist workers.
            self._send_json({"data": [], "meta": {"correlation_id": "test-correlation-id"}})
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
        self.server.recorded_request_paths = []  # type: ignore[attr-defined]
        self.server.settings_attributes = _settings_attributes()  # type: ignore[attr-defined]
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
        server = t.cast(t.Any, self.server)
        return t.cast(list[dict[str, t.Any]], server.recorded_payloads)

    @property
    def recorded_request_paths(self) -> list[str]:
        assert self.server is not None
        server = t.cast(t.Any, self.server)
        return t.cast(list[str], server.recorded_request_paths)

    def count_requests(self, path: str) -> int:
        return self.recorded_request_paths.count(path)

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
    env.pop("PYTEST_XDIST_WORKER", None)
    env.pop("PYTEST_XDIST_WORKER_COUNT", None)
    for name in (
        DD_TEST_OPTIMIZATION_ENV_DATA_FILE,
        DD_TEST_OPTIMIZATION_MANIFEST_FILE,
        DD_TEST_OPTIMIZATION_PAYLOADS_IN_FILES,
        TEST_UNDECLARED_OUTPUTS_DIR,
        # The outer test process may set kill switches that override the backend settings we configure per test;
        # drop them so the mock backend's response is authoritative.
        "DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED",
    ):
        env.pop(name, None)
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


class TestXdistManifestMode:
    def test_controller_generates_manifest_workers_avoid_backend_fanout(
        self, mock_server: MockCIVisibilityServer, test_project: Path
    ) -> None:
        settings = _settings_attributes()
        settings["itr_enabled"] = True
        settings["tests_skipping"] = True
        assert mock_server.server is not None
        mock_server.server.settings_attributes = settings  # type: ignore[attr-defined]

        marker_dir = test_project / "xdist_markers"
        (test_project / "conftest.py").write_text(
            textwrap.dedent(f"""\
                import os
                from pathlib import Path

                MARKER_DIR = Path({str(marker_dir)!r})

                def pytest_configure(config):
                    worker = os.environ.get("PYTEST_XDIST_WORKER")
                    manifest = os.environ.get("DD_TEST_OPTIMIZATION_MANIFEST_FILE", "")
                    MARKER_DIR.mkdir(exist_ok=True)
                    (MARKER_DIR / (worker or "controller")).write_text(manifest)
            """)
        )
        (test_project / "test_a.py").write_text(
            textwrap.dedent(f"""\
                import os
                from pathlib import Path

                MARKER_DIR = Path({str(marker_dir)!r})

                def _record_test(name):
                    worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")
                    with (MARKER_DIR / (worker + "-tests")).open("a") as f:
                        f.write(name + "\\n")
                    if worker != "controller":
                        from ddtrace.testing.internal.offline_mode import get_offline_mode

                        offline_mode = get_offline_mode()
                        (MARKER_DIR / (worker + "-manifest-mode")).write_text(
                            f"enabled={{offline_mode.manifest_enabled}}\\n"
                            f"dir={{offline_mode.test_optimization_dir}}\\n"
                        )

                def test_one():
                    _record_test("test_one")
                    assert True

                def test_two():
                    _record_test("test_two")
                    assert True

                def test_three():
                    _record_test("test_three")
                    assert True

                def test_four():
                    _record_test("test_four")
                    assert True
            """)
        )
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        result = _run_pytest_subprocess(test_project, "-n", "2", env=env)

        assert result.returncode == 0, f"pytest failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        # Every worker inherited the controller-generated manifest ...
        worker_manifests = [path.read_text() for path in marker_dir.glob("gw[0-9]")]
        assert len(worker_manifests) == 2
        assert all(XDIST_MANIFEST_DIR_PREFIX in manifest for manifest in worker_manifests), worker_manifests
        # ... and actually ran in manifest mode instead of querying the backend.
        worker_manifest_modes = [path.read_text() for path in marker_dir.glob("gw*-manifest-mode")]
        assert len(worker_manifest_modes) == 2
        assert all("enabled=True" in mode for mode in worker_manifest_modes), worker_manifest_modes
        assert all(XDIST_MANIFEST_DIR_PREFIX in mode for mode in worker_manifest_modes), worker_manifest_modes

        # Only the controller talked to the backend.
        assert mock_server.count_requests("/api/v2/libraries/tests/services/setting") == 1
        assert mock_server.count_requests("/api/v2/ci/tests/skippable") == 1

        worker_test_counts = [len(path.read_text().splitlines()) for path in marker_dir.glob("gw*-tests")]
        assert len(worker_test_counts) == 2
        assert sum(worker_test_counts) == 4
        assert all(1 <= count <= 3 for count in worker_test_counts)

        # The generated manifest cache is a private temp directory, removed when the session ends.
        manifest_dir = Path(worker_manifests[0]).parent
        assert not manifest_dir.exists(), manifest_dir


class TestResolveInheritedManifestEnv:
    """A generated manifest is only honored by the controller that wrote it and by the workers it spawned."""

    @pytest.fixture(autouse=True)
    def not_a_worker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default to a controller; the worker tests opt back in."""
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    def test_keeps_manifest_generated_by_this_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        manifest = f"/tmp/{XDIST_MANIFEST_DIR_PREFIX}{os.getpid()}_abc/manifest.txt"
        monkeypatch.setenv(DD_TEST_OPTIMIZATION_MANIFEST_FILE, manifest)

        resolve_inherited_manifest_env()

        assert os.environ[DD_TEST_OPTIMIZATION_MANIFEST_FILE] == manifest

    def test_keeps_manifest_generated_by_parent_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        manifest = f"/tmp/{XDIST_MANIFEST_DIR_PREFIX}{os.getppid()}_abc/manifest.txt"
        monkeypatch.setenv(DD_TEST_OPTIMIZATION_MANIFEST_FILE, manifest)

        resolve_inherited_manifest_env()

        assert os.environ[DD_TEST_OPTIMIZATION_MANIFEST_FILE] == manifest

    def test_discards_manifest_generated_by_unrelated_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        manifest = f"/tmp/{XDIST_MANIFEST_DIR_PREFIX}1_abc/manifest.txt"
        monkeypatch.setenv(DD_TEST_OPTIMIZATION_MANIFEST_FILE, manifest)

        resolve_inherited_manifest_env()

        assert DD_TEST_OPTIMIZATION_MANIFEST_FILE not in os.environ

    def test_keeps_user_provided_manifest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bazel-style manifests are not ours to second-guess, whatever process exported them."""
        manifest = "/some/workspace/.testoptimization/manifest.txt"
        monkeypatch.setenv(DD_TEST_OPTIMIZATION_MANIFEST_FILE, manifest)

        resolve_inherited_manifest_env()

        assert os.environ[DD_TEST_OPTIMIZATION_MANIFEST_FILE] == manifest

    def test_logs_when_worker_reads_the_controller_manifest(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The INFO line is how you tell at a glance that workers are not querying the backend."""
        manifest_dir = tmp_path / f"{XDIST_MANIFEST_DIR_PREFIX}{os.getppid()}_abc"
        manifest_dir.mkdir()
        manifest = manifest_dir / "manifest.txt"
        manifest.write_text("version = 1\n")
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
        monkeypatch.setenv(DD_TEST_OPTIMIZATION_MANIFEST_FILE, str(manifest))
        info = mock.Mock()
        monkeypatch.setattr(xdist_module.log, "info", info)

        resolve_inherited_manifest_env()

        assert info.call_count == 1
        assert "is reading backend data cached by its controller" in info.call_args.args[0]
        assert os.environ[DD_TEST_OPTIMIZATION_MANIFEST_FILE] == str(manifest)

    def test_warns_when_worker_cannot_read_generated_manifest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
        monkeypatch.setenv(
            DD_TEST_OPTIMIZATION_MANIFEST_FILE, f"/tmp/{XDIST_MANIFEST_DIR_PREFIX}{os.getppid()}_abc/manifest.txt"
        )
        warning = mock.Mock()
        monkeypatch.setattr(xdist_module.log, "warning", warning)

        resolve_inherited_manifest_env()

        assert warning.call_count == 1
        assert "could not read the manifest generated by its controller" in warning.call_args.args[0]

    def test_silent_when_worker_has_no_generated_manifest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A worker of a session that never generated a manifest (e.g. Bazel, or a failed write) is not a problem."""
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
        monkeypatch.delenv(DD_TEST_OPTIMIZATION_MANIFEST_FILE, raising=False)
        warning = mock.Mock()
        monkeypatch.setattr(xdist_module.log, "warning", warning)

        resolve_inherited_manifest_env()

        assert warning.call_count == 0


def _session_manager_without_errors() -> mock.Mock:
    """A SessionManager stand-in whose backend fetches all succeeded."""
    return mock.Mock(configuration_errors={})


class TestGenerateXdistManifestFailures:
    """Generating the manifest is an optimization: a failure must degrade to online mode, never break the session."""

    @pytest.fixture(autouse=True)
    def controller_with_xdist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        monkeypatch.delenv(DD_TEST_OPTIMIZATION_MANIFEST_FILE, raising=False)

    def test_returns_none_when_the_controller_fetch_had_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Degraded data must not be handed to the workers: leave them online so each retries on its own."""
        session_manager = mock.Mock(configuration_errors={"test.configuration_error.settings": "true"})
        mkdtemp = mock.Mock()
        monkeypatch.setattr(tempfile, "mkdtemp", mkdtemp)

        assert generate_xdist_manifest(session_manager, ["-n", "2"]) is None
        assert mkdtemp.call_count == 0
        assert DD_TEST_OPTIMIZATION_MANIFEST_FILE not in os.environ

    def test_returns_none_when_temp_dir_cannot_be_created(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tempfile, "mkdtemp", mock.Mock(side_effect=OSError("read-only file system")))

        assert generate_xdist_manifest(_session_manager_without_errors(), ["-n", "2"]) is None
        assert DD_TEST_OPTIMIZATION_MANIFEST_FILE not in os.environ

    def test_returns_none_and_cleans_up_when_cache_cannot_be_written(self, monkeypatch: pytest.MonkeyPatch) -> None:
        created_dirs: list[str] = []
        real_mkdtemp = tempfile.mkdtemp

        def record_mkdtemp(**kwargs: t.Any) -> str:
            created_dirs.append(real_mkdtemp(**kwargs))
            return created_dirs[-1]

        monkeypatch.setattr(tempfile, "mkdtemp", record_mkdtemp)
        monkeypatch.setattr(xdist_module, "write_manifest_cache", mock.Mock(side_effect=OSError("no space left")))

        assert generate_xdist_manifest(_session_manager_without_errors(), ["-n", "2"]) is None
        assert DD_TEST_OPTIMIZATION_MANIFEST_FILE not in os.environ
        assert created_dirs and not Path(created_dirs[0]).exists()


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
    """Verify that _DD_CIVISIBILITY_PARTIAL_FLUSH_MIN_SPANS mitigates crash data loss.

    These tests prove the workaround works by running the same crash scenario
    with and without the env var and comparing the outcomes.
    """

    def test_partial_flush_before_and_after(self, test_project: Path) -> None:
        """Same crash scenario, two runs: without and with _DD_CIVISIBILITY_PARTIAL_FLUSH_MIN_SPANS=1.

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

            env = _make_env(server_with.url, extra={"_DD_CIVISIBILITY_PARTIAL_FLUSH_MIN_SPANS": "1"})
            _run_pytest_subprocess(test_project, "-n", "1", "--max-worker-restart", "1", "-p", "no:randomly", env=env)

            names_with = [e["content"]["meta"]["test.name"] for e in server_with.get_test_events()]

        # --- Assertions: the contrast ---
        # Without eager flushing: the passing test is lost.
        assert "test_passes_before_crash" not in names_without, (
            f"Without eager flushing, the event should be lost. Got: {names_without}"
        )

        # With eager flushing: the passing test is saved.
        assert "test_passes_before_crash" in names_with, (
            f"With _DD_CIVISIBILITY_PARTIAL_FLUSH_MIN_SPANS=1, the event should be saved. Got: {names_with}"
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

        env = _make_env(mock_server.url, extra={"_DD_CIVISIBILITY_PARTIAL_FLUSH_MIN_SPANS": "1"})
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


def test_mock_settings_payload_is_parseable() -> None:
    """Regression: the mock settings payload must be complete enough for ``Settings.from_attributes`` to parse.

    If a required field is missing, ``Settings.from_attributes`` raises ``KeyError``, ``APIClient.get_settings``
    swallows it and returns a default ``Settings()`` (every feature disabled). That would silently make all of the
    xdist tests above run against fallback defaults instead of the settings the mock reports.
    """
    from ddtrace.testing.internal.settings_data import Settings

    settings = Settings.from_attributes(_settings_attributes())

    # The fields whose absence previously triggered the KeyError fallback.
    assert settings.early_flake_detection.slow_test_retries_5s == 10
    assert settings.early_flake_detection.faulty_session_threshold == 30
    assert settings.test_management.attempt_to_fix_retries == 20


class TestXdistCoverageUpload:
    """Under xdist the controller holds the merged coverage report; workers should not also upload.

    pytest-cov ships every worker's coverage data to the controller and merges it there
    (``DistMaster.finish`` -> ``cov.combine``) before ``pytest_sessionfinish`` runs, so the controller's
    report is complete.  Workers therefore skip the upload and the controller uploads once -- N+1 partial
    uploads become one complete upload.  This is the clean, low-risk case; without pytest-cov nothing merges
    across processes and every process must still upload its own partial report.
    """

    def test_coverage_uploaded_once_with_pytest_cov(
        self, mock_server: MockCIVisibilityServer, test_project: Path
    ) -> None:
        pytest.importorskip("pytest_cov")

        settings = _settings_attributes()
        settings["coverage_report_upload_enabled"] = True
        assert mock_server.server is not None
        mock_server.server.settings_attributes = settings  # type: ignore[attr-defined]

        (test_project / "test_a.py").write_text("def test_one():\n    assert True\n")
        (test_project / "test_b.py").write_text("def test_two():\n    assert True\n")
        _git_commit(test_project)

        env = _make_env(mock_server.url)
        result = _run_pytest_subprocess(test_project, "-n", "2", "--cov", env=env)

        assert result.returncode == 0, f"pytest failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        # The controller uploads one merged report; workers skip. Exactly one upload, not one per process.
        assert mock_server.count_requests("/api/v2/cicovreprt") == 1, (
            f"expected a single coverage upload from the controller, "
            f"got {mock_server.count_requests('/api/v2/cicovreprt')}"
        )
