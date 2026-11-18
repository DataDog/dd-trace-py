from __future__ import annotations

import builtins
import hashlib
import importlib
import json
import subprocess
import sys
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.api_client import APIClient
from ddtrace.testing.internal.constants import ITRSkippingLevel
from ddtrace.testing.internal.http import BackendResult
from ddtrace.testing.internal.pytest._tia import _document
from ddtrace.testing.internal.pytest._tia import configuration_hash
from ddtrace.testing.internal.tia.processes import ProcessMonitor
from ddtrace.testing.internal.tia.selection import Graph
from ddtrace.testing.internal.tia.selection import load_graph
from ddtrace.testing.internal.tia.selection import select_tests
from ddtrace.testing.internal.tracer_api.coverage import coverage_collection
from ddtrace.testing.internal.tracer_api.coverage import is_coverage_installed
from tests.testing.internal.pytest.utils import assert_stats
from tests.testing.mocks import mock_api_client_settings
from tests.testing.mocks import setup_standard_mocks


SHA = "a" * 40


@pytest.fixture
def tia_backend(pytester, monkeypatch):
    monkeypatch.setenv("DD_TIA_ENABLED", "1")
    monkeypatch.setenv("DD_CIVISIBILITY_ENABLED", "true")
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    client = mock_api_client_settings()
    client.lookup_tia_map.return_value = None
    client.store_tia_map.return_value = True
    git = Mock()
    git.get_workspace_path.return_value = str(pytester.path)
    git.get_repository_url.return_value = "https://example.com/repo.git"
    git.get_git_path.return_value = pytester.path / ".git" / ".testoptimization" / "tia"
    git.get_tia_commits.return_value = [SHA]
    git.get_commit_sha.return_value = SHA
    git.get_tia_changed_files.return_value = set()
    git.get_tia_untracked_files.return_value = set()
    git.is_tia_worktree_clean.return_value = True
    git.is_shallow_repository.return_value = False
    git.has_commit.return_value = True
    with (
        patch("ddtrace.testing.internal.pytest._tia.Git", return_value=git),
        patch("ddtrace.testing.internal.session_manager.APIClient", return_value=client),
        setup_standard_mocks(workspace_path=str(pytester.path)),
    ):
        yield client, git


def _baseline(client, tests, shared=(), failed=(), always=()):
    document = _document(Graph(SHA, tests, set(shared), set(failed), set(always)))
    client.lookup_tia_map.return_value = document


def test_tia_env_enables_without_ddtrace_flag(pytester, tia_backend):
    client, git = tia_backend
    pytester.makepyfile("def test_one(): pass")
    original_import_module = importlib.import_module
    result = pytester.inline_run()
    result.assertoutcome(passed=1)
    client.lookup_tia_map.assert_called_once()
    args = client.lookup_tia_map.call_args.args
    assert args[0].configurations is None
    assert len(args[0].env_hash) == 64
    assert args[0].repository_id == "https://example.com/repo.git"
    assert len(args[0].suite_id) == 64
    assert args[1] == [SHA]
    document = client.store_tia_map.call_args.args[1]
    graph = load_graph(document["map"], document)
    assert graph.tests == {
        "test_tia_env_enables_without_ddtrace_flag.py::test_one": {"test_tia_env_enables_without_ddtrace_flag.py"}
    }
    assert not getattr(builtins.__import__, "_filemon_tracking", False)
    assert importlib.import_module is original_import_module


def test_selects_affected_failed_new_and_subprocess_tests(pytester, tia_backend):
    client, git = tia_backend
    pytester.makepyfile(
        test_example="""
        def test_affected(): pass
        def test_unaffected(): pass
        def test_failed(): pass
        def test_new(): pass
        def test_subprocess(): pass
    """
    )
    tests = {
        "test_example.py::test_affected": {"changed.py"},
        "test_example.py::test_unaffected": {"unchanged.py"},
        "test_example.py::test_failed": set(),
        "test_example.py::test_subprocess": set(),
    }
    _baseline(client, tests, failed=["test_example.py::test_failed"], always=["test_example.py::test_subprocess"])
    git.get_tia_changed_files.return_value = {"changed.py"}
    result = pytester.inline_run()
    result.assertoutcome(passed=4)
    assert sum(len(call.items) for call in result.getcalls("pytest_deselected")) == 1
    saved = client.store_tia_map.call_args.args[1]
    graph = load_graph(saved["map"], saved)
    assert graph.tests["test_example.py::test_unaffected"] == {"unchanged.py"}
    assert not graph.failed


def test_no_changes_deselects_all_successfully(pytester, tia_backend):
    client, _ = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    _baseline(client, {"test_example.py::test_one": {"test_example.py"}})
    result = pytester.inline_run()
    result.assertoutcome()
    assert sum(len(call.items) for call in result.getcalls("pytest_deselected")) == 1
    assert result.ret == 0


@pytest.mark.parametrize("changed", [None, {"conftest.py"}])
def test_uncertain_or_shared_changes_run_all(pytester, tia_backend, changed):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    _baseline(client, {"test_example.py::test_one": {"test_example.py"}}, shared={"conftest.py"})
    git.get_tia_changed_files.return_value = changed
    pytester.inline_run().assertoutcome(passed=1)


def test_unmapped_changes_only_run_new_tests(pytester, tia_backend):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_old(): pass\ndef test_new(): pass")
    _baseline(client, {"test_example.py::test_old": {"test_example.py"}})
    git.get_tia_changed_files.return_value = {"new_module.py", "notes.txt"}
    result = pytester.inline_run()
    result.assertoutcome(passed=1)
    assert sum(len(call.items) for call in result.getcalls("pytest_deselected")) == 1


def test_new_conftest_affects_existing_tests(pytester, tia_backend):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    pytester.makeconftest("VALUE = 1")
    _baseline(client, {"test_example.py::test_one": {"test_example.py"}})
    git.get_tia_changed_files.return_value = {"conftest.py"}
    pytester.inline_run().assertoutcome(passed=1)


@pytest.mark.parametrize("args", [("-k", "one"), ("-m", "chosen"), ("test_example.py::test_one",), ("--lf",)])
def test_explicit_selectors_are_honored(pytester, tia_backend, args):
    client, _ = tia_backend
    pytester.makeini("[pytest]\nmarkers = chosen")
    pytester.makepyfile(test_example="import pytest\n@pytest.mark.chosen\ndef test_one(): pass")
    _baseline(client, {"test_example.py::test_one": {"test_example.py"}})
    pytester.inline_run(*args).assertoutcome(passed=1)


def test_cached_runtime_import_closure_and_fixture_dependencies(pytester, tia_backend):
    client, _ = tia_backend
    pytester.makepyfile(tia_leaf="VALUE = 1", tia_parent="import tia_leaf", tia_fixture="VALUE = 2")
    pytester.makeconftest("""
        import pytest
        @pytest.fixture(scope="session")
        def shared():
            import tia_fixture
            return tia_fixture.VALUE
    """)
    pytester.makepyfile(
        test_example="""
        def test_first(shared):
            import tia_parent
            assert shared == 2
        def test_second(shared):
            import tia_parent
            assert shared == 2
    """
    )
    try:
        pytester.inline_run().assertoutcome(passed=2)
        document = client.store_tia_map.call_args.args[1]
        graph = load_graph(document["map"], document)
        for deps in graph.tests.values():
            assert {"tia_parent.py", "tia_leaf.py", "test_example.py"} <= deps
        assert {"conftest.py", "tia_fixture.py"} <= graph.import_deps
    finally:
        for name in ("tia_leaf", "tia_parent", "tia_fixture"):
            sys.modules.pop(name, None)


def test_early_stop_does_not_baseline_unexecuted_tests(pytester, tia_backend):
    client, _ = tia_backend
    pytester.makepyfile(test_example="def test_a(): assert False\ndef test_b(): pass")
    pytester.inline_run("-x", "-p", "no:randomly").assertoutcome(failed=1)
    document = client.store_tia_map.call_args.args[1]
    graph = load_graph(document["map"], document)
    assert "test_example.py::test_b" not in graph.tests
    assert "test_example.py::test_a" in graph.failed


@pytest.mark.parametrize("args", [("--collect-only",), ()])
def test_no_upload_from_collection_only_or_dirty_worktree(pytester, tia_backend, args):
    client, git = tia_backend
    pytester.makepyfile("def test_one(): pass")
    if not args:
        git.is_tia_worktree_clean.return_value = False
    pytester.inline_run(*args)
    client.store_tia_map.assert_not_called()


@pytest.mark.parametrize("before", [False, True])
def test_untracked_report_does_not_prevent_publication(pytester, tia_backend, before):
    client, git = tia_backend
    if before:
        (pytester.path / "coverage.xml").write_text("<coverage/>")
    pytester.makepyfile(
        test_example="from pathlib import Path\ndef test_one(): Path('coverage.xml').write_text('<coverage/>')"
    )
    git.get_tia_untracked_files.return_value = {"coverage.xml"}
    pytester.inline_run().assertoutcome(passed=1)
    client.store_tia_map.assert_called_once()
    client.lookup_tia_map.reset_mock()
    pytester.inline_run().assertoutcome()
    client.lookup_tia_map.assert_not_called()


@pytest.mark.parametrize("untracked", [{"test_example.py"}, {"conftest.py"}, None])
def test_untracked_inputs_or_unknown_state_prevent_publication(pytester, tia_backend, untracked):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    pytester.makeconftest("VALUE = 1")
    git.get_tia_untracked_files.return_value = untracked
    pytester.inline_run().assertoutcome(passed=1)
    client.store_tia_map.assert_not_called()
    assert not list((pytester.path / ".git").rglob(f"{SHA}.json"))


def test_tracked_changes_during_execution_prevent_publication(pytester, tia_backend):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    git.is_tia_worktree_clean.side_effect = [True, False]
    pytester.inline_run().assertoutcome(passed=1)
    client.store_tia_map.assert_not_called()


def test_kill_switch_prevents_tia(pytester, tia_backend, monkeypatch):
    client, _ = tia_backend
    monkeypatch.setenv("DD_CIVISIBILITY_ENABLED", "false")
    pytester.makepyfile("def test_one(): pass")
    pytester.inline_run().assertoutcome(passed=1)
    client.lookup_tia_map.assert_not_called()


def test_disabled_has_no_tia_requests(pytester, tia_backend, monkeypatch):
    client, _ = tia_backend
    monkeypatch.delenv("DD_TIA_ENABLED")
    pytester.makepyfile("def test_one(): pass")
    pytester.inline_run("--ddtrace").assertoutcome(passed=1)
    client.lookup_tia_map.assert_not_called()


def test_collect_error_restores_hooks(pytester, tia_backend):
    client, _ = tia_backend
    original = builtins.__import__
    original_import_module = importlib.import_module
    installed = is_coverage_installed()
    pytester.makepyfile("raise RuntimeError('collection failed')")
    assert pytester.inline_run().ret != 0
    assert builtins.__import__ is original
    assert importlib.import_module is original_import_module
    assert is_coverage_installed() == installed
    client.store_tia_map.assert_not_called()


def test_process_monitor_restores_process_entry_points():
    original = subprocess.Popen.__init__
    monitor = ProcessMonitor()
    try:
        monitor.activate()
        monitor.phase = "test"
        monitor.current_nodeid = "test"
        subprocess.run([sys.executable, "-c", "pass"], check=True)
        assert monitor.spawn_tests == {"test"}
    finally:
        monitor.deactivate()
    assert subprocess.Popen.__init__ is original


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 999),
        ("files", [None]),
        ("files", ["../outside.py"]),
        ("files", ["/outside.py"]),
        ("tests", {"test": [-1]}),
        ("tests", {"test": [True]}),
        ("tests", {"test": [5]}),
        ("import_deps", [99]),
        ("failed", "test"),
        ("always", {}),
    ],
)
def test_invalid_maps_run_everything(field, value):
    document = _document(Graph(SHA, {"test": {"module.py"}}, set(), set(), set()))
    document["map"][field] = value
    graph = load_graph(document["map"], document)
    assert graph is None
    assert select_tests(["test"], graph, set()).selected == ["test"]


def test_configuration_hash_is_stable_across_checkouts_and_configuration_order(tmp_path):
    def config(root):
        return Mock(
            invocation_params=Mock(args=("tests", "-q"), dir=root),
            rootpath=root,
            inicfg={"addopts": "-ra"},
        )

    first = tmp_path / "checkout-a"
    second = tmp_path / "checkout-b"
    a = configuration_hash(config(first), first, {"os": "linux", "arch": "arm64"})
    b = configuration_hash(config(second), second, {"arch": "arm64", "os": "linux"})
    assert a == b
    assert configuration_hash(config(second), second, {"arch": "amd64", "os": "linux"}) != a
    changed = config(second)
    changed.invocation_params.args = ("other_tests", "-q")
    assert configuration_hash(changed, second, {"arch": "arm64", "os": "linux"}) != a


def test_git_error_during_finish_preserves_pytest_result(pytester, tia_backend):
    client, git = tia_backend
    pytester.makepyfile("def test_one(): pass")
    git.is_tia_worktree_clean.side_effect = [True, OSError("git unavailable")]
    result = pytester.inline_run()
    result.assertoutcome(passed=1)
    assert result.ret == 0
    client.store_tia_map.assert_not_called()


def test_generated_map_is_reused_without_lookup_when_upload_fails(pytester, tia_backend):
    client, _ = tia_backend
    client.store_tia_map.return_value = False
    pytester.makepyfile(test_example="def test_one(): pass")
    pytester.inline_run().assertoutcome(passed=1)
    document = client.store_tia_map.call_args.args[1]
    assert not load_graph(document["map"], document).always
    client.lookup_tia_map.assert_called_once()
    client.lookup_tia_map.reset_mock()

    result = pytester.inline_run()
    result.assertoutcome()
    assert sum(len(call.items) for call in result.getcalls("pytest_deselected")) == 1
    assert result.ret == 0
    client.lookup_tia_map.assert_not_called()


def test_remote_map_is_cached_for_later_collection_only_runs(pytester, tia_backend):
    client, _ = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    _baseline(client, {"test_example.py::test_one": {"test_example.py"}})
    pytester.inline_run("--collect-only")
    client.lookup_tia_map.assert_called_once()
    client.lookup_tia_map.reset_mock()

    pytester.inline_run("--collect-only")
    client.lookup_tia_map.assert_not_called()
    client.store_tia_map.assert_not_called()


def test_cached_map_from_recent_ancestor_is_used(pytester, tia_backend):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    pytester.inline_run().assertoutcome(passed=1)
    client.lookup_tia_map.reset_mock()
    git.get_tia_commits.return_value = ["b" * 40, SHA]
    git.get_commit_sha.return_value = "b" * 40
    git.get_tia_changed_files.return_value = {"test_example.py"}

    pytester.inline_run().assertoutcome(passed=1)
    client.lookup_tia_map.assert_not_called()
    git.get_tia_changed_files.assert_called_with(SHA)


def test_shallow_cache_miss_fetches_history_before_backend_lookup(pytester, tia_backend):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    commits = [SHA] + [f"{index:040x}" for index in range(19)]
    git.is_shallow_repository.return_value = True
    git.unshallow_repository.return_value = Mock(return_code=0)
    git.get_tia_commits.side_effect = [[SHA], commits]
    pytester.inline_run().assertoutcome(passed=1)
    git.unshallow_repository.assert_called_once_with(SHA, full_history=True)
    assert client.lookup_tia_map.call_args.args[1] == commits


def test_unshallow_rechecks_local_ancestor_cache_before_backend(pytester, tia_backend):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    pytester.inline_run().assertoutcome(passed=1)
    client.lookup_tia_map.reset_mock()
    head = "b" * 40
    git.get_commit_sha.return_value = head
    git.get_tia_commits.side_effect = [[head], [head, SHA]]
    git.is_shallow_repository.return_value = True
    git.unshallow_repository.return_value = Mock(return_code=0)
    pytester.inline_run().assertoutcome()
    git.unshallow_repository.assert_called_once_with(head, full_history=True)
    client.lookup_tia_map.assert_not_called()


def test_local_hit_with_available_source_does_not_fetch(pytester, tia_backend):
    _, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    pytester.inline_run().assertoutcome(passed=1)
    git.is_shallow_repository.return_value = True
    pytester.inline_run().assertoutcome()
    git.unshallow_repository.assert_not_called()


def test_local_hit_with_missing_source_fetches_before_diff(pytester, tia_backend):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    _baseline(client, {"test_example.py::test_one": {"test_example.py"}})
    pytester.inline_run("--collect-only")
    client.lookup_tia_map.reset_mock()
    git.is_shallow_repository.return_value = True
    git.has_commit.return_value = False
    git.unshallow_repository.return_value = Mock(return_code=0)
    git.get_tia_changed_files.side_effect = lambda source: set() if git.unshallow_repository.called else None
    pytester.inline_run("--collect-only")
    git.unshallow_repository.assert_called_once_with(SHA, full_history=True)
    client.lookup_tia_map.assert_not_called()


def test_unshallow_opt_out_uses_available_history(pytester, tia_backend, monkeypatch):
    client, git = tia_backend
    monkeypatch.setenv("DD_CIVISIBILITY_GIT_UNSHALLOW_ENABLED", "false")
    pytester.makepyfile(test_example="def test_one(): pass")
    git.is_shallow_repository.return_value = True
    pytester.inline_run().assertoutcome(passed=1)
    git.unshallow_repository.assert_not_called()
    assert client.lookup_tia_map.call_args.args[1] == [SHA]


@pytest.mark.parametrize("failure", [1, OSError("fetch unavailable")])
def test_unshallow_failure_uses_available_history(pytester, tia_backend, failure):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    git.is_shallow_repository.return_value = True
    if isinstance(failure, Exception):
        git.unshallow_repository.side_effect = failure
    else:
        git.unshallow_repository.return_value = Mock(return_code=failure)
    pytester.inline_run().assertoutcome(passed=1)
    assert client.lookup_tia_map.call_args.args[1] == [SHA]
    client.store_tia_map.assert_called_once()


def test_cache_write_failure_does_not_prevent_remote_upload(pytester, tia_backend):
    client, _ = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    with patch("ddtrace.testing.internal.tia.store._write_json_atomic", side_effect=PermissionError("read only")):
        result = pytester.inline_run()
    result.assertoutcome(passed=1)
    assert result.ret == 0
    client.store_tia_map.assert_called_once()


def test_cache_is_not_used_for_a_different_command(pytester, tia_backend):
    client, _ = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    pytester.inline_run().assertoutcome(passed=1)
    client.lookup_tia_map.reset_mock()

    pytester.inline_run("-q").assertoutcome(passed=1)
    client.lookup_tia_map.assert_called_once()


@pytest.mark.parametrize("invalidate", ["corrupt", "stale"])
def test_invalid_local_cache_falls_back_to_backend(pytester, tia_backend, invalidate):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    pytester.inline_run().assertoutcome(passed=1)
    client.lookup_tia_map.reset_mock()
    if invalidate == "corrupt":
        for path in git.get_git_path.return_value.glob("*/*.json"):
            path.write_text("{")
    else:
        git.get_tia_commits.return_value = ["b" * 40]
        git.get_commit_sha.return_value = "b" * 40

    pytester.inline_run().assertoutcome(passed=1)
    client.lookup_tia_map.assert_called_once()


def test_remote_source_commit_controls_diff_and_survives_local_cache(pytester, tia_backend):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    source = "b" * 40
    document = _document(Graph(source, {"test_example.py::test_one": {"test_example.py"}}, set(), set(), set()))
    document["matched_commit"] = SHA
    client.lookup_tia_map.return_value = document
    git.get_tia_changed_files.return_value = {"test_example.py"}

    pytester.inline_run("--collect-only")
    git.get_tia_changed_files.assert_called_with(source)
    client.lookup_tia_map.reset_mock()
    git.get_tia_changed_files.reset_mock()

    pytester.inline_run("--collect-only")
    client.lookup_tia_map.assert_not_called()
    git.get_tia_changed_files.assert_called_with(source)


def test_metadata_uses_itr_repository_tags_and_existing_test_command(pytester, tia_backend):
    client, git = tia_backend
    pytester.makepyfile(test_example="def test_one(): pass")
    command = "pytest tests/unit -q"
    with (
        setup_standard_mocks(workspace_path=str(pytester.path)),
        patch(
            "ddtrace.testing.internal.session_manager.get_env_tags",
            return_value={
                "git.repository_url": "https://user:token@example.com/other-repo.git",
            },
        ),
        patch("ddtrace.testing.internal.pytest.plugin._get_test_command", return_value=command),
    ):
        pytester.inline_run().assertoutcome(passed=1)
    metadata = client.lookup_tia_map.call_args.args[0]
    assert metadata.repository_id == "https://example.com/other-repo.git"
    assert metadata.suite_id == hashlib.sha256(command.encode()).hexdigest()
    assert metadata.language == "python"
    assert metadata.compatibility_version == 0
    git.get_repository_url.assert_not_called()


def test_pytest_uses_artifact_transport_and_then_local_cache(pytester, tia_backend):
    client, git = tia_backend
    source = "b" * 40
    pytester.makepyfile(test_example="def test_one(): pass\ndef test_two(): pass")
    document = _document(
        Graph(
            source,
            {
                "test_example.py::test_one": {"test_example.py", "changed.py"},
                "test_example.py::test_two": {"test_example.py"},
            },
            set(),
            set(),
            set(),
        )
    )
    headers = {"x-dd-tia-git-commit-sha": SHA, "x-dd-tia-source-commit": source}
    response = Mock(status_code=200)
    response.header.side_effect = headers.get
    connector = Mock()

    def request(method, path, **kwargs):
        if method == "POST":
            return BackendResult(response=response, response_body=json.dumps(document).encode())
        assert method == "PUT"
        assert json.loads(kwargs["data"])["base_commit"] == SHA
        return BackendResult(response=Mock(status_code=201), parsed_response={"blob_id": "blob", "deduplicated": False})

    connector.request.side_effect = request
    setup = Mock()
    setup.get_connector_for_subdomain.return_value = connector
    api = APIClient("tests", "ci", {}, ITRSkippingLevel.TEST, {}, setup, Mock())
    client.lookup_tia_map.side_effect = api.lookup_tia_map
    client.store_tia_map.side_effect = api.store_tia_map
    git.get_tia_changed_files.return_value = {"changed.py"}

    result = pytester.inline_run()
    result.assertoutcome(passed=1)
    assert sum(len(call.items) for call in result.getcalls("pytest_deselected")) == 1
    git.get_tia_changed_files.assert_called_with(source)
    git.get_tia_changed_files.return_value = set()
    pytester.inline_run().assertoutcome()
    lookups = [call for call in connector.request.call_args_list if call.args[0] == "POST"]
    assert len(lookups) == 1
    assert lookups[0].args[1] == "/api/unstable/ci/tia/artifacts/lookup"
    metadata = json.loads(lookups[0].kwargs["data"])["data"]["attributes"]
    assert metadata["repository_id"] == "https://example.com/repo.git"
    assert len(metadata["suite_id"]) == len(metadata["env_hash"]) == 64


@pytest.mark.parametrize("file_level", ["true", "false"])
def test_execution_coverage_from_joined_thread(pytester, tia_backend, monkeypatch, file_level):
    client, _ = tia_backend
    monkeypatch.setenv("_DD_COVERAGE_FILE_LEVEL", file_level)
    pytester.makepyfile(tia_thread_worker="def compute(): return 42")
    pytester.makepyfile(
        test_example="""
        import builtins
        import threading
        from tia_thread_worker import compute

        def test_thread():
            assert not getattr(builtins.__import__, "_filemon_tracking", False)
            results = []
            thread = threading.Thread(target=lambda: results.append(compute()))
            thread.start()
            thread.join()
            assert results == [42]
    """
    )
    try:
        pytester.inline_run().assertoutcome(passed=1)
        document = client.store_tia_map.call_args.args[1]
        graph = load_graph(document["map"], document)
        assert {"tia_thread_worker.py", "test_example.py"} <= graph.tests["test_example.py::test_thread"]
        assert not graph.always
    finally:
        sys.modules.pop("tia_thread_worker", None)


def test_tia_and_itr_share_the_per_test_collector(pytester, tia_backend):
    client, _ = tia_backend
    client.get_settings.return_value.coverage_enabled = True
    pytester.makepyfile(test_example="def test_one(): pass")
    with patch("ddtrace.testing.internal.pytest.plugin.coverage_collection", wraps=coverage_collection) as collect:
        pytester.inline_run().assertoutcome(passed=1)
    assert collect.call_count == 1
    document = client.store_tia_map.call_args.args[1]
    assert load_graph(document["map"], document).tests["test_example.py::test_one"] == {"test_example.py"}


def test_tia_does_not_uninstall_a_borrowed_collector(pytester, tia_backend):
    pytester.makepyfile(test_example="def test_one(): pass")
    with (
        patch("ddtrace.testing.internal.pytest._tia.is_coverage_installed", return_value=True),
        patch("ddtrace.testing.internal.pytest._tia.install_coverage") as install,
        patch("ddtrace.testing.internal.pytest._tia.uninstall_coverage") as uninstall,
    ):
        pytester.inline_run().assertoutcome(passed=1)
    install.assert_not_called()
    uninstall.assert_not_called()


def test_tia_keeps_coverage_from_every_retry(pytester, tia_backend):
    client, _ = tia_backend
    client.get_settings.return_value.auto_test_retries.enabled = True
    pytester.makepyfile(tia_retry_first="VALUE = 1", tia_retry_last="VALUE = 2")
    pytester.makepyfile(
        test_example="""
        attempts = 0

        def test_retry():
            global attempts
            attempts += 1
            if attempts == 1:
                import tia_retry_first
                assert tia_retry_first.VALUE == 0
            import tia_retry_last
            assert tia_retry_last.VALUE == 2
    """
    )
    try:
        assert_stats(pytester.inline_run(), passed=1)
        document = client.store_tia_map.call_args.args[1]
        graph = load_graph(document["map"], document)
        assert {"test_example.py", "tia_retry_first.py", "tia_retry_last.py"} <= graph.tests[
            "test_example.py::test_retry"
        ]
    finally:
        sys.modules.pop("tia_retry_first", None)
        sys.modules.pop("tia_retry_last", None)
