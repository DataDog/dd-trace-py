"""
API for code coverage collection for use by ddtrace.testing.

The rest of ddtrace.testing should only use the interface exposed in this file to set up code coverage and get
coverage data.
"""

import contextlib
import functools
import logging
import os
from pathlib import Path
import typing as t

from ddtrace.contrib.internal.coverage.patch import get_coverage_instance
from ddtrace.contrib.internal.coverage.patch import get_coverage_percentage as _get_coverage_percentage
from ddtrace.contrib.internal.coverage.patch import patch as patch_coverage
from ddtrace.contrib.internal.coverage.patch import run_coverage_report
from ddtrace.contrib.internal.coverage.patch import unpatch as unpatch_coverage
from ddtrace.contrib.internal.coverage.utils import _build_path_aliases
from ddtrace.contrib.internal.coverage.utils import _is_coverage_invoked_by_coverage_run
from ddtrace.contrib.internal.coverage.utils import _is_coverage_patched
from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.coverage.coverage_lines import CoverageLines
import ddtrace.internal.coverage.installer
from ddtrace.internal.utils.paths import relative_path
from ddtrace.testing.internal.logging import catch_and_log_exceptions


log = logging.getLogger(__name__)


log = logging.getLogger(__name__)


class _AliasMatcher:
    """Picklable admission predicate for installed copies of the library under test.

    In the parent process the aliases are resolved from the active coverage
    configuration on every call, so an instance started later (e.g. pytest-cov
    with a custom --cov-config) is honored. When the matcher is pickled to a
    spawned child process (multiprocessing spawn or forkserver), the resolved
    aliases are snapshotted with it: the child has no parent coverage instance,
    and default configuration discovery there may not find a custom
    --cov-config's aliases.
    """

    def __init__(self, workspace_path: Path) -> None:
        self._workspace_path_str = str(workspace_path)
        # Resolved (pattern, regex, result) alias specs; set when pickled to a
        # child process and restored on unpickling.
        self._alias_specs: t.Optional[tuple[tuple[str, t.Any, str], ...]] = None

    def __getstate__(self) -> dict[str, t.Any]:
        aliases = _coverage_path_aliases()
        specs = tuple(aliases.aliases) if aliases is not None else ()
        return {"workspace_path": self._workspace_path_str, "alias_specs": specs}

    def __setstate__(self, state: dict[str, t.Any]) -> None:
        self._workspace_path_str = state["workspace_path"]
        self._alias_specs = state["alias_specs"] or None

    def __call__(self, code_path: Path) -> bool:
        specs = self._alias_specs
        if specs is None:
            aliases = _coverage_path_aliases()
            if aliases is None:
                return False
            specs = aliases.aliases
        matched, mapped = _map_path_with_specs(specs, str(code_path), self._workspace_path_str)
        if not matched or mapped is None:
            return False
        # A broad alias (e.g. a whole-workspace "source" group) can map an
        # installed dependency to the location it already occupies. Only
        # aliases that relocate the file to a repository path admit it past
        # the collector's exclusions; identity mappings leave dependencies
        # excluded.
        return Path(self._workspace_path_str) / mapped.lstrip("/") != Path(code_path)


def install_coverage(workspace_path: Path, file_level_coverage: bool = False) -> None:
    # Installed copies of the library under test (e.g. a wheel in a virtual
    # environment's site-packages) normally fail the collector's gates: they
    # live under site-packages and possibly outside the workspace. The matcher
    # admits copies that a coverage [paths] alias maps back to this repository,
    # so the coverage report can attribute them to the repository.
    ddtrace.internal.coverage.installer.install(
        include_paths=[workspace_path],
        collect_import_time_coverage=True,
        file_level_coverage=file_level_coverage,
        alias_matcher=_AliasMatcher(workspace_path),
    )
    ModuleCodeCollector.start_coverage()


def uninstall_coverage() -> None:
    ModuleCodeCollector.uninstall()


_UNSET = object()  # sentinel distinguishing "no aliases built yet" from "built from no instance"
_alias_source: t.Any = _UNSET
_path_aliases: t.Optional[t.Any] = None
_alias_generation: int = 0  # bumped whenever the aliases are rebuilt


def _coverage_path_aliases() -> t.Optional[t.Any]:
    """The [paths] aliases of the active coverage configuration, or None if
    coverage.py is unavailable or no aliases are configured.

    Aliases come from the active coverage instance when one is running (e.g. a
    pytest-cov session with a custom --cov-config), and from the default
    configuration discovery otherwise. The aliases are rebuilt whenever the
    active instance changes, so an instance started after an earlier lookup
    (e.g. pytest-cov starting during pytest_configure, after a module was
    imported during conftest loading) is honored for the rest of the session.
    """
    global _alias_source, _path_aliases, _alias_generation
    instance = get_coverage_instance()
    if instance is not _alias_source:
        try:
            _path_aliases = _build_path_aliases(instance)
        except Exception:
            log.debug("Could not build coverage [paths] aliases", exc_info=True)
            _path_aliases = None
        _alias_source = instance
        _alias_generation += 1
    return _path_aliases


def clear_coverage_path_caches() -> None:
    """Reset the coverage path remapping caches for a new coverage session.

    The aliases (and the per-path results) depend on the active coverage
    instance and the configuration discovered from the working directory, so
    they must not be reused across sessions that may change either.
    """
    global _alias_source, _path_aliases, _alias_generation
    _alias_source = _UNSET
    _path_aliases = None
    # Monotonic: never reuse results cached by an earlier session.
    _alias_generation += 1
    _relative_coverage_path_cached.cache_clear()


def _map_path_with_specs(
    specs: t.Iterable[tuple[str, t.Any, str]], absolute_path: str, relative_to: str
) -> tuple[bool, t.Optional[str]]:
    """Whether one of the given alias specs matches the file, and its upload path."""
    for _pattern, regex, result in specs:
        if match := regex.match(absolute_path):
            mapped = absolute_path.replace(match[0], result, 1).removeprefix("./")
            if Path(mapped).is_absolute():
                mapped_path = Path(mapped)
            else:
                mapped_path = Path(os.path.normpath(str(Path.cwd() / mapped)))
            relative = relative_path(mapped_path, Path(relative_to))
            if relative is None:
                return True, None  # mapped outside the current repository
            return True, f"/{relative}"
    return False, None


def _alias_mapped_path(absolute_path: str, relative_to: str) -> tuple[bool, t.Optional[str]]:
    """Whether a [paths] alias matches the file, and its upload path.

    The first matching alias wins. Relative destinations are resolved against
    the working directory, and every destination is reported relative to the
    workspace; a destination outside the workspace cannot be attributed to the
    repository, so the file is dropped (matched with a None path).
    """
    aliases = _coverage_path_aliases()
    if aliases is None:
        return False, None
    return _map_path_with_specs(aliases.aliases, absolute_path, relative_to)


def _relative_coverage_path(absolute_path: str, relative_to: str) -> t.Optional[str]:
    # Refreshing the alias generation first means results cached under an
    # earlier active coverage instance (or none) are never reused after the
    # instance changes.
    _coverage_path_aliases()
    return _relative_coverage_path_cached(absolute_path, relative_to, _alias_generation)


@functools.lru_cache(maxsize=65536)
def _relative_coverage_path_cached(absolute_path: str, relative_to: str, _generation: int) -> t.Optional[str]:
    # Aliases take precedence so installed copies of repository files are reported
    # at their repository paths, matching what coverage combine does for reports.
    matched, mapped = _alias_mapped_path(absolute_path, relative_to)
    if matched:
        # A None mapping falls outside the repository, so the file is dropped
        # rather than uploaded with a path the backend cannot attribute.
        return mapped
    relative = relative_path(Path(absolute_path), Path(relative_to))
    if relative is None:
        return None  # covered file does not belong to current repo
    return f"/{relative}"


_FILE_LEVEL_BITMAP = bytes(CoverageLines.from_list([0]).to_bytes())


class CoverageData:
    def __init__(self) -> None:
        self._covered_lines: t.Optional[dict[str, CoverageLines]] = None
        self._covered_file_paths: t.Optional[t.Iterable[str]] = None

    def get_coverage_bitmaps(self, relative_to: Path) -> t.Iterable[tuple[str, bytes]]:
        relative_to_str = str(relative_to)

        if self._covered_file_paths is not None:
            # A checkout copy and an installed copy can alias to the same upload
            # filename; report it once.
            seen: set[str] = set()
            for absolute_path in self._covered_file_paths:
                path_str = _relative_coverage_path(absolute_path, relative_to_str)
                if path_str is not None and path_str not in seen:
                    seen.add(path_str)
                    yield path_str, _FILE_LEVEL_BITMAP
            return

        if not self._covered_lines:
            return

        # A checkout copy and an installed copy (e.g. reported by a child process)
        # can alias to the same upload filename; union their covered lines so the
        # file has a single bitmap entry instead of split duplicates.
        bitmaps: dict[str, CoverageLines] = {}
        for absolute_path, covered_lines in self._covered_lines.items():
            path_str = _relative_coverage_path(absolute_path, relative_to_str)
            if path_str is None:
                continue
            if path_str in bitmaps:
                bitmaps[path_str].update(covered_lines)
            else:
                bitmaps[path_str] = covered_lines

        for path_str, covered_lines in bitmaps.items():
            yield path_str, covered_lines.to_bytes()


@contextlib.contextmanager
def coverage_collection() -> t.Generator[CoverageData, None, None]:
    with ModuleCodeCollector.CollectInContext() as coverage_collector:
        coverage_data = CoverageData()
        yield coverage_data
        if ModuleCodeCollector.file_level_coverage_enabled():
            coverage_data._covered_file_paths = coverage_collector.get_covered_file_paths()
        else:
            coverage_data._covered_lines = coverage_collector.get_covered_lines()


def install_coverage_percentage():
    """
    Patch coverage.py to obtain coverage percentage from pytest-cov.
    """
    patch_coverage()


def uninstall_coverage_percentage():
    """
    Undo patching of coverage.py.
    """
    unpatch_coverage()


@catch_and_log_exceptions()
def get_coverage_percentage(pytest_cov_status: bool) -> t.Optional[float]:
    """
    Retrieve coverage percentage collected during a pytest-cov test session, if available.

    This retrieves the coverage percentage from coverage.py patching, coverage report upload,
    or generates a report if using 'coverage run' without pytest-cov.
    """
    # Generate report if using coverage run without pytest-cov
    if not pytest_cov_status and _is_coverage_invoked_by_coverage_run() and _is_coverage_patched():
        run_coverage_report()

    # Get cached percentage (works for pytest-cov, coverage run, and coverage report upload)
    lines_pct_value = _get_coverage_percentage()

    if lines_pct_value is None:
        log.debug("Unable to retrieve coverage data for the session span")
        return None

    if not isinstance(lines_pct_value, (float, int)):
        log.warning(
            "Unexpected format for total covered percentage: type=%s.%s, value=%r",
            type(lines_pct_value).__module__,
            type(lines_pct_value).__name__,
            lines_pct_value,
        )
        return None

    log.debug("Code coverage: %s%%", lines_pct_value)
    return float(lines_pct_value)
