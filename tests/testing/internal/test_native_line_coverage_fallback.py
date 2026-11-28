"""Where native line coverage does not run: what is collected instead, and how the test session says so."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import textwrap
from unittest import mock

import pytest

from ddtrace.internal.coverage import native_lines
from ddtrace.testing.internal.test_data import TestTag


_REPO_ROOT = Path(__file__).resolve().parents[3]
_FALLBACK = TestTag.CODE_COVERAGE_FALLBACK
_SELF_TEST = TestTag.CODE_COVERAGE_NATIVE_SELF_TEST_PREFIX

_PY_GE_312 = sys.version_info >= (3, 12)
_GIL_BUILD = not sysconfig.get_config_var("Py_GIL_DISABLED")
_HAS_EXTENSION = _PY_GE_312 and importlib.util.find_spec("ddtrace.internal.coverage._linecov") is not None

needs_sys_monitoring = pytest.mark.skipif(not _PY_GE_312, reason="native line coverage needs sys.monitoring")
needs_gil_build = pytest.mark.skipif(not _GIL_BUILD, reason="native line coverage does not run on free-threaded builds")
needs_extension = pytest.mark.skipif(
    not (_HAS_EXTENSION and _GIL_BUILD), reason="needs the native line coverage extension and a build with the GIL"
)

# The variables that choose how native line coverage runs, which each test sets itself.
_COVERAGE_VARIABLES = (
    "_DD_COVERAGE_NATIVE_LINES",
    "_DD_COVERAGE_FILE_LEVEL",
    "_DD_COVERAGE_BLOCK_PROBES",
    "_DD_COVERAGE_INTERNAL_REARM",
    "_DD_COVERAGE_INSTRUCTION_PROBES",
)

# Runs one pytest session with coverage on or off in the settings, after the given preparation, and prints the
# test session's native line coverage tags, this module's "unavailable" log records, the coverage records the
# Python collector made, and whether the extension and the child process bootstrap were loaded.
_SESSION_SCRIPT = """
import contextlib
import json
import logging
import os
import sys
from unittest.mock import patch

sys.path.insert(0, {repo_root!r})

{prepare}

from ddtrace.internal.coverage import native_lines


class Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append([record.levelname, record.getMessage()])


handler = Records()
native_logger = logging.getLogger(native_lines.__name__)
native_logger.addHandler(handler)
native_logger.setLevel(logging.DEBUG)

import pytest

from ddtrace.testing.internal.pytest.plugin import TestOptPlugin
from ddtrace.testing.internal.tracer_api.coverage import CoverageData
from tests.testing.mocks import EventCapture
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks

records = []


def put_test_coverage(self, test_run, coverage_data):
    if not isinstance(coverage_data, CoverageData) or coverage_data._native is not None:
        records.append(["native", None])
        return
    for path, bitmap in coverage_data.get_coverage_bitmaps(relative_to=self.manager.workspace_path):
        lines = sorted(i * 8 + b for i, byte in enumerate(bitmap) for b in range(8) if byte & (0x80 >> b))
        records.append([path, lines])


with contextlib.ExitStack() as stack:
    stack.enter_context(
        patch(
            "ddtrace.testing.internal.session_manager.APIClient",
            return_value=mock_api_client_settings(coverage_enabled={coverage}),
        )
    )
    stack.enter_context(setup_standard_mocks())
    stack.enter_context(patch.object(TestOptPlugin, "_put_test_coverage", put_test_coverage))
    events = stack.enter_context(EventCapture.capture())
    exit_code = pytest.main(["--ddtrace", "-p", "no:cacheprovider", "-p", "no:randomly", "-q", "test_one.py"])

[session_end] = events.events_by_type("test_session_end")
meta = session_end["content"]["meta"]
mapped = None
if os.path.exists("/proc/self/maps"):
    with open("/proc/self/maps") as maps:
        mapped = any("_linecov" in line for line in maps)
print(json.dumps({{
    "exit_code": int(exit_code),
    "tags": {{k: v for k, v in meta.items() if k == {fallback!r} or k.startswith({self_test!r})}},
    "logs": [r for r in handler.records if "unavailable" in r[1]],
    "records": records,
    "extension_loaded": sys.modules.get("ddtrace.internal.coverage._linecov") is not None,
    "extension_mapped": mapped,
    "child_bootstrap_loaded": "ddtrace.internal.coverage.child_bootstrap._ddtrace_child_coverage" in sys.modules,
}}))
"""

_EXTENSION_NOT_FOUND = """
sys.modules["ddtrace.internal.coverage._linecov"] = None
"""

_EXTENSION_FAILS_TO_LOAD = """
import importlib.abc
import importlib.machinery


class FailingLoader(importlib.abc.Loader):
    def create_module(self, spec):
        raise ImportError("simulated load failure")

    def exec_module(self, module):
        pass


class FailingFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "ddtrace.internal.coverage._linecov":
            return importlib.machinery.ModuleSpec(name, FailingLoader())
        return None


sys.meta_path.insert(0, FailingFinder())
"""

_START_FAILS = """
from ddtrace.internal.coverage import _linecov


def failing_init(**kwargs):
    raise RuntimeError("simulated start failure")


_linecov.init = failing_init
"""

_NO_FREE_TOOL_SLOT = """
for slot in (4, 3, 1):
    sys.monitoring.use_tool_id(slot, "another tool")
"""

# native_lines decides when it is imported. What it imports is loaded first, so that only it sees the build as
# free-threaded.
_FREE_THREADED_BUILD = """
import sysconfig

import ddtrace.internal.forksafe
import ddtrace.internal.logger
import ddtrace.internal.module
import ddtrace.internal.settings
import ddtrace.internal.utils.formats

get_config_var = sysconfig.get_config_var
sysconfig.get_config_var = lambda name: 1 if name == "Py_GIL_DISABLED" else get_config_var(name)
from ddtrace.internal.coverage import native_lines
sysconfig.get_config_var = get_config_var
"""

_WITHOUT_THE_CACHE_READ = """
from ddtrace.internal.coverage import native_lines

start = native_lines.init


def start_without_the_cache_read(tool_id):
    started = start(tool_id)
    if started:
        native_lines._linecov.set_cached_owner(False)
    return started


native_lines.init = start_without_the_cache_read
"""


def _run_session(tmp_path: Path, prepare: str = "", coverage: bool = True, env=None) -> dict:
    (tmp_path / "lib.py").write_text("def double(x):\n    return 2 * x\n")
    (tmp_path / "test_one.py").write_text("import lib\n\n\ndef test_one():\n    assert lib.double(2) == 4\n")
    script = _SESSION_SCRIPT.format(
        repo_root=str(_REPO_ROOT),
        prepare=textwrap.dedent(prepare),
        coverage=coverage,
        fallback=_FALLBACK,
        self_test=_SELF_TEST,
    )
    run_env = {
        name: value
        for name, value in os.environ.items()
        if name not in _COVERAGE_VARIABLES and not name.startswith("PYTEST_XDIST")
    }
    run_env.update({"DD_API_KEY": "test-key", "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "false", **(env or {})})
    # A subprocess: coverage registers a sys.monitoring tool and process-wide state.
    result = subprocess.run(
        [sys.executable, "-c", script], env=run_env, cwd=tmp_path, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    assert outcome["exit_code"] == 0, result.stdout + result.stderr
    return outcome


def _unavailable(reason: str, instead: str) -> str:
    return f"native line coverage unavailable: {reason} ({instead})"


def _logged(level: str, reason: str, instead: str, error: str = "") -> list:
    return [[level, f"Native line coverage unavailable: {reason} ({instead}){': ' + error if error else ''}"]]


def _file_level(records: list) -> bool:
    return bool(records) and all(lines == [0] for _, lines in records)


def test_coverage_off_session_loads_no_native_module(tmp_path) -> None:
    """A session that collects no coverage loads neither the extension nor the child process bootstrap, and its
    session span carries no native line coverage tag.
    """
    outcome = _run_session(tmp_path, coverage=False)

    assert outcome["extension_loaded"] is False
    assert outcome["extension_mapped"] in (False, None)
    assert outcome["child_bootstrap_loaded"] is False
    assert outcome["tags"] == {}
    assert outcome["logs"] == []
    assert outcome["records"] == []


@needs_extension
def test_native_session_records_its_self_tests(tmp_path) -> None:
    """While native line collection runs, the session span says which path each start-up self-test chose."""
    outcome = _run_session(tmp_path)

    tags = outcome["tags"]
    assert _FALLBACK not in tags
    assert tags[_SELF_TEST + "rearm"] in ("internal", "public")
    assert tags[_SELF_TEST + "events"] in ("INSTRUCTION probes", "LINE events")
    assert tags[_SELF_TEST + "owner_read"] in ("cached", "PyContextVar_Get")
    assert _SELF_TEST + "block_probes" not in tags
    assert outcome["logs"] == []
    assert outcome["extension_loaded"] is True
    assert outcome["records"] and all(kind == "native" for kind, _ in outcome["records"])


@needs_extension
@pytest.mark.parametrize(
    "env, prepare, expected",
    [
        ({"_DD_COVERAGE_INTERNAL_REARM": "false"}, "", {"rearm": "public", "events": "LINE events"}),
        ({"_DD_COVERAGE_INSTRUCTION_PROBES": "false"}, "", {"events": "LINE events"}),
        ({}, _WITHOUT_THE_CACHE_READ, {"owner_read": "PyContextVar_Get"}),
    ],
    ids=["public-rearm", "line-events", "no-cache-read"],
)
def test_self_test_tags_name_the_path_taken(tmp_path, env, prepare, expected) -> None:
    outcome = _run_session(tmp_path, prepare=prepare, env=env)

    assert {name: outcome["tags"][_SELF_TEST + name] for name in expected} == expected


@needs_extension
def test_block_probes_tag_when_they_were_asked_for(tmp_path) -> None:
    outcome = _run_session(tmp_path, env={"_DD_COVERAGE_BLOCK_PROBES": "true"})

    value = outcome["tags"][_SELF_TEST + "block_probes"]
    # Block probes exist on CPython 3.12 only.
    assert value in (("off",) if sys.version_info >= (3, 13) else ("on", "off"))


@needs_sys_monitoring
@needs_gil_build
@pytest.mark.parametrize(
    "prepare, reason, error",
    [
        (_EXTENSION_NOT_FOUND, "extension not found", ""),
        (_EXTENSION_FAILS_TO_LOAD, "extension failed to load", "ImportError: simulated load failure"),
    ],
    ids=["not-found", "load-failed"],
)
def test_missing_extension_falls_back_to_file_level_coverage(tmp_path, prepare, reason, error) -> None:
    outcome = _run_session(tmp_path, prepare=prepare)

    assert outcome["tags"] == {_FALLBACK: _unavailable(reason, "file-level coverage")}
    assert outcome["logs"] == _logged("WARNING", reason, "file-level coverage", error)
    assert _file_level(outcome["records"]), outcome["records"]
    assert outcome["extension_loaded"] is False
    assert outcome["child_bootstrap_loaded"] is False


@needs_extension
def test_failed_start_falls_back_to_file_level_coverage(tmp_path) -> None:
    """A failed start does not fall back to the Python line collector: coverage is file-level, and says why."""
    outcome = _run_session(tmp_path, prepare=_START_FAILS)

    reason = "start failed"
    assert outcome["tags"] == {_FALLBACK: _unavailable(reason, "file-level coverage")}
    assert outcome["logs"] == _logged("WARNING", reason, "file-level coverage", "RuntimeError: simulated start failure")
    assert _file_level(outcome["records"]), outcome["records"]


@needs_extension
def test_no_free_tool_slot_gives_no_coverage(tmp_path) -> None:
    outcome = _run_session(tmp_path, prepare=_NO_FREE_TOOL_SLOT)

    reason = "no free sys.monitoring tool slot"
    assert outcome["tags"] == {_FALLBACK: _unavailable(reason, "no coverage")}
    assert outcome["logs"] == _logged("WARNING", reason, "no coverage")
    assert outcome["records"] == []


@needs_sys_monitoring
def test_free_threaded_build_is_reported(tmp_path) -> None:
    """Simulated on a build with the GIL: the build is checked before the extension is looked for."""
    outcome = _run_session(tmp_path, prepare=_FREE_THREADED_BUILD)

    reason = "free-threaded build"
    assert outcome["tags"] == {_FALLBACK: _unavailable(reason, "file-level coverage")}
    assert outcome["logs"] == _logged("INFO", reason, "file-level coverage")
    assert _file_level(outcome["records"]), outcome["records"]
    assert outcome["extension_loaded"] is False


@pytest.mark.skipif(_PY_GE_312, reason="Python before 3.12 only")
def test_python_before_312_keeps_the_python_collector(tmp_path) -> None:
    outcome = _run_session(tmp_path)

    reason = "Python before 3.12"
    assert outcome["tags"] == {_FALLBACK: _unavailable(reason, "lines from the Python collector")}
    assert outcome["logs"] == _logged("INFO", reason, "lines from the Python collector")
    lib_lines = [lines for path, lines in outcome["records"] if path.endswith("lib.py")]
    assert lib_lines and all(2 in lines for lines in lib_lines), outcome["records"]


@needs_sys_monitoring
def test_switching_native_collection_off_is_not_reported(tmp_path) -> None:
    """Turning native line coverage off is the user's choice: the session says nothing about it."""
    outcome = _run_session(tmp_path, env={"_DD_COVERAGE_NATIVE_LINES": "false"})

    assert outcome["tags"] == {}
    assert outcome["logs"] == []
    assert outcome["extension_loaded"] is False
    assert _file_level(outcome["records"]), outcome["records"]


def test_windows_is_reported_at_info(monkeypatch) -> None:
    """Windows cannot be made to happen on another system: this checks its marker and log level only."""
    log = mock.Mock()
    monkeypatch.setattr(native_lines, "_reason", native_lines._WINDOWS)
    monkeypatch.setattr(native_lines, "_error", None)
    monkeypatch.setattr(native_lines, "log", log)

    native_lines.log_unavailable(True)

    assert native_lines.unavailable(True) == _unavailable("Windows", "file-level coverage")
    log.info.assert_called_once_with(
        "Native line coverage unavailable: %s (%s)%s", "Windows", "file-level coverage", ""
    )
    log.warning.assert_not_called()
