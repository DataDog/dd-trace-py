"""Coverage-based test impact analysis for a single pytest process."""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
from pathlib import Path
import platform
import sys
from types import ModuleType
import typing as t

import pytest

from ddtrace.internal.coverage.util import is_file_level_coverage_enabled
from ddtrace.internal.settings import env
from ddtrace.testing.internal.api_client import APIClient
from ddtrace.testing.internal.constants import TEST_OPTIMIZATION_DIR
from ddtrace.testing.internal.git import Git
from ddtrace.testing.internal.logging import catch_and_log_exceptions
from ddtrace.testing.internal.pytest.utils import _test_file
from ddtrace.testing.internal.tia.artifacts import ArtifactMetadata
from ddtrace.testing.internal.tia.paths import to_repo_rel
from ddtrace.testing.internal.tia.processes import ProcessMonitor
from ddtrace.testing.internal.tia.selection import DATA_VERSION
from ddtrace.testing.internal.tia.selection import Graph
from ddtrace.testing.internal.tia.selection import load_graph
from ddtrace.testing.internal.tia.selection import select_tests
from ddtrace.testing.internal.tia.store import LocalMapStore
from ddtrace.testing.internal.tracer_api.coverage import CoverageData
from ddtrace.testing.internal.tracer_api.coverage import coverage_for_files
from ddtrace.testing.internal.tracer_api.coverage import install_coverage
from ddtrace.testing.internal.tracer_api.coverage import is_coverage_installed
from ddtrace.testing.internal.tracer_api.coverage import uninstall_coverage
from ddtrace.testing.internal.utils import _filter_sensitive_info


log = logging.getLogger(__name__)


def configuration_hash(config: pytest.Config, root: Path, configurations: dict[str, str]) -> str:
    """Identify the invocation using repository-relative working and root directories."""
    data = {
        "version": DATA_VERSION,
        "collector": "dd-coverage-v2",
        "command": list(config.invocation_params.args),
        "addopts": env.get("PYTEST_ADDOPTS", ""),
        "root": config.rootpath.resolve().relative_to(root).as_posix(),
        "cwd": Path(config.invocation_params.dir).resolve().relative_to(root).as_posix(),
        "ini": dict(config.inicfg),
        "configurations": configurations,
        "python": platform.python_version(),
        "pytest": pytest.__version__,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _selectors_active(config: pytest.Config) -> bool:
    options = config.option
    return bool(
        getattr(options, "keyword", None)
        or getattr(options, "markexpr", None)
        or getattr(options, "lf", False)
        or getattr(options, "failedfirst", False)
        or any("::" in arg for arg in config.args)
    )


def _document(graph: Graph) -> dict[str, t.Any]:
    files = sorted(graph.import_deps.union(*(deps for deps in graph.tests.values())))
    indices = {path: index for index, path in enumerate(files)}
    return {
        "base_commit": graph.base_commit,
        "map": {
            "version": DATA_VERSION,
            "files": files,
            "tests": {nodeid: sorted(indices[path] for path in deps) for nodeid, deps in sorted(graph.tests.items())},
            "import_deps": sorted(indices[path] for path in graph.import_deps),
            "failed": sorted(graph.failed),
            "always": sorted(graph.always),
        },
    }


class TIAPlugin:
    def __init__(
        self,
        config: pytest.Config,
        client: APIClient,
        configurations: dict[str, str],
        test_command: str,
        repository_url: t.Optional[str],
    ) -> None:
        self.client = client
        config.add_cleanup(client.close)
        self.git = Git(cwd=str(config.rootpath))
        workspace = self.git.get_workspace_path()
        if not workspace:
            raise ValueError("TIA requires a Git repository")
        self.root = Path(workspace).resolve()
        self.git = Git(cwd=str(self.root))
        self.hash = configuration_hash(config, self.root, configurations)
        repository_id = _filter_sensitive_info(repository_url or self.git.get_repository_url())
        if not repository_id:
            raise ValueError("TIA requires a repository URL")
        # EVP rejects JSON punctuation in query strings; runtime configurations are already part of env_hash.
        self.metadata = ArtifactMetadata(
            repository_id=repository_id,
            suite_id=hashlib.sha256(test_command.encode("utf-8")).hexdigest(),
            env_hash=self.hash,
        )
        self.commits = self.git.get_tia_commits()
        if not self.commits:
            raise ValueError("TIA requires a local commit")
        self.clean = self.git.is_tia_worktree_clean()
        self.store = LocalMapStore(self.git.get_git_path(f"{TEST_OPTIMIZATION_DIR}/tia"))
        document = self.store.load(self.hash, self.commits)
        if document is None:
            document = client.lookup_tia_map(self.metadata, self.commits)
            graph = load_graph(document.get("map"), document) if isinstance(document, dict) else None
            if (
                isinstance(document, dict)
                and graph is not None
                and document.get("matched_commit", graph.base_commit) in self.commits
            ):
                self.store.save(self.hash, document)
            else:
                document = None
        self.graph = load_graph(document.get("map"), document) if document is not None else None
        self.changed = (
            self.git.get_tia_changed_files(self.graph.base_commit)
            if self.graph is not None and self.graph.base_commit is not None
            else None
        )
        self._preloaded_files = self._loaded_files()
        self._owns_coverage = not is_coverage_installed()
        self._test_deps: dict[str, set[str]] = {}
        self._shared_roots: set[Path] = set()
        self.monitor = ProcessMonitor()
        self.items: dict[str, str] = {}
        self.selected: set[str] = set()
        self.finished: set[str] = set()
        self.called: set[str] = set()
        self.failed: set[str] = set()
        self.always: set[str] = set()
        self.deselected = 0
        self.recording_failed = False
        self._dynamic_imports_active = False
        self._original_import_module = importlib.import_module
        self._import_module_wrapper: t.Optional[t.Callable[..., ModuleType]] = None
        config.add_cleanup(self.close)
        if self._owns_coverage:
            install_coverage(self.root, file_level_coverage=is_file_level_coverage_enabled())
        if not is_coverage_installed():
            raise ValueError("TIA requires dd-coverage collection")
        self.monitor.activate()

        def import_module(name: str, package: t.Optional[str] = None) -> ModuleType:
            module = self._original_import_module(name, package)
            if not self._dynamic_imports_active:
                return module
            try:
                filename = getattr(module, "__file__", None)
                if isinstance(filename, str) and to_repo_rel(filename, self.root) is not None:
                    # Cached dynamic imports execute neither module code nor an import bytecode.
                    path = Path(filename)
                    if self.monitor.phase == "test" and self.monitor.current_nodeid is not None:
                        files = coverage_for_files([path]).get_covered_files(self.root)
                        self._test_deps.setdefault(self.monitor.current_nodeid, set()).update(
                            file.lstrip("/") for file in files
                        )
                    else:
                        self._shared_roots.add(path)
            except Exception:
                self.recording_failed = True
                log.debug("Could not record a TIA dynamic import", exc_info=True)
            return module

        self._import_module_wrapper = import_module
        self._dynamic_imports_active = True
        importlib.import_module = import_module

    def _loaded_files(self) -> set[str]:
        files = set()
        for module in list(sys.modules.values()):
            filename = getattr(module, "__file__", None)
            if isinstance(filename, str) and (relative := to_repo_rel(filename, self.root)) is not None:
                files.add(relative)
        return files

    def _stop_dynamic_imports(self) -> None:
        self._dynamic_imports_active = False
        if importlib.import_module is self._import_module_wrapper:
            importlib.import_module = self._original_import_module

    def close(self) -> None:
        self._stop_dynamic_imports()
        self.monitor.deactivate()
        if self._owns_coverage and is_coverage_installed():
            uninstall_coverage()

    def record_coverage(self, item: pytest.Item, coverage: CoverageData) -> None:
        try:
            files = {path.lstrip("/") for path in coverage.get_covered_files(self.root)}
            if not files:
                self.always.add(item.nodeid)
            files.update(
                path.lstrip("/")
                for path in coverage_for_files([self.root / self.items[item.nodeid]]).get_covered_files(self.root)
            )
            self._test_deps.setdefault(item.nodeid, set()).update(files)
        except Exception:
            self.recording_failed = True
            log.debug("Could not record TIA coverage", exc_info=True)

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection(self) -> None:
        self.monitor.phase = "collect"

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config: pytest.Config, items: list[pytest.Item]) -> None:
        for item in items:
            rel = to_repo_rel(_test_file(item), self.root)
            if rel is None:
                self.recording_failed = True
                return
            self.items[item.nodeid] = rel
        changed = self.changed
        if self.graph is not None and changed:
            known = self.graph.import_deps.union(*(deps for deps in self.graph.tests.values()))
            # New source/configuration can alter resolution without a previously recorded edge.
            if any(path not in known for path in changed):
                changed = None
        if _selectors_active(config) or not self.monitor.valid:
            changed = None
        selection = select_tests(list(self.items), self.graph, changed)
        self.selected = set(selection.selected)
        self.selected.update(
            item.nodeid for item in items if item.get_closest_marker("datadog_itr_unskippable") is not None
        )
        dropped = [item for item in items if item.nodeid not in self.selected]
        if dropped:
            config.hook.pytest_deselected(items=dropped)
            items[:] = [item for item in items if item.nodeid in self.selected]
        self.deselected = len(dropped)
        log.debug("TIA: %s", selection.reason)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> t.Generator[None, None, None]:
        self.monitor.phase = "test"
        self.monitor.current_nodeid = item.nodeid
        try:
            yield
        finally:
            self.monitor.current_nodeid = None
            self.monitor.phase = "import"

    @pytest.hookimpl(hookwrapper=True)
    def pytest_fixture_setup(self) -> t.Generator[None, None, None]:
        # A process launched by a shared fixture can affect tests which reuse its cached result.
        phase = self.monitor.phase
        self.monitor.phase = "fixture"
        try:
            yield
        finally:
            self.monitor.phase = phase

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.failed:
            self.failed.add(report.nodeid)
        if report.skipped:
            self.always.add(report.nodeid)
        if report.when == "call":
            self.called.add(report.nodeid)
        if report.when == "teardown" and report.nodeid in self.called:
            self.finished.add(report.nodeid)

    @pytest.hookimpl(tryfirst=True)
    @catch_and_log_exceptions()
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        self._stop_dynamic_imports()
        self.monitor.deactivate()
        if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED and self.deselected and not session.items:
            session.exitstatus = pytest.ExitCode.OK
        if (
            self.recording_failed
            or not self.monitor.valid
            or session.config.option.collectonly
            or exitstatus not in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED, pytest.ExitCode.NO_TESTS_COLLECTED)
            or not self.clean
            or not self.git.is_tia_worktree_clean()
            or self.git.get_commit_sha() != self.commits[0]
        ):
            return
        old = self.graph
        tests = {nodeid: deps for nodeid, deps in old.tests.items() if nodeid in self.items} if old else {}
        shared = set(old.import_deps) if old else set()
        # Previously imported code may not have been instrumented. Preserve all loaded repository files in that case.
        if self._preloaded_files:
            shared.update(self._loaded_files())
        # Pytest also uses import_module to load test modules; their roots remain per-test.
        test_files = set(self.items.values())
        shared_roots = {path for path in self._shared_roots if to_repo_rel(str(path), self.root) not in test_files}
        for plugin in session.config.pluginmanager.get_plugins():
            filename = getattr(plugin, "__file__", None)
            if isinstance(filename, str):
                rel = to_repo_rel(filename, self.root)
                if rel:
                    # pytest_plugins strings do not create import edges from conftest files.
                    shared_roots.add(Path(filename))
        shared.update(path.lstrip("/") for path in coverage_for_files(shared_roots).get_covered_files(self.root))
        if session.config.inipath:
            rel = to_repo_rel(session.config.inipath, self.root)
            if rel:
                shared.add(rel)
        for nodeid in self.selected:
            if nodeid in self.finished and nodeid in self._test_deps:
                tests[nodeid] = self._test_deps[nodeid] | {self.items[nodeid]}
            else:
                # Interrupted, disabled, or setup-failed tests must never acquire a fresh baseline.
                tests.pop(nodeid, None)
        always = (old.always - self.finished if old else set()) | self.monitor.spawn_tests | self.always
        if self.monitor.spawn_shared:
            always.update(tests)
        failed = (old.failed - self.finished if old else set()) | self.failed
        graph = Graph(self.commits[0], tests, shared, failed, always)
        if tests:
            document = _document(graph)
            self.store.save(self.hash, document)
            self.client.store_tia_map(self.metadata, document)
