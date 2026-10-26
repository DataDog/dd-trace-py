from pathlib import Path
from unittest import mock

from coverage import Coverage
import pytest

from ddtrace.internal.coverage.code import ModuleCodeCollector
import ddtrace.testing.internal.tracer_api.coverage as tracer_api_coverage
from ddtrace.testing.internal.tracer_api.coverage import clear_coverage_path_caches
from ddtrace.testing.internal.tracer_api.coverage import coverage_collection


def _make_site_packages(tmp_path: Path) -> Path:
    site_packages = tmp_path / ".cache/uv-test-environments/abc/lib/python3.12/site-packages/covered_pkg"
    site_packages.mkdir(parents=True)
    return site_packages


def test_get_coverage_bitmaps() -> None:
    with mock.patch.object(ModuleCodeCollector, "CollectInContext") as mock_collect_in_context:
        mock_collect_in_context().__enter__().get_covered_lines.return_value = {
            "/repo/path/foo.py": mock.Mock(to_bytes=lambda: b"abc123"),
            "/repo/path/foo/bar.py": mock.Mock(to_bytes=lambda: b"abc456"),
            "/not/repo/path/baz.py": mock.Mock(to_bytes=lambda: b"abc789"),
        }

        with coverage_collection() as coverage_data:
            pass

        assert list(coverage_data.get_coverage_bitmaps(Path("/repo/path"))) == [
            ("/foo.py", b"abc123"),
            ("/foo/bar.py", b"abc456"),
        ]


def test_get_coverage_bitmaps_paths_alias_remap(tmp_path, monkeypatch) -> None:
    """Wheel-installed library files report their repository path via [paths] aliases.

    CI test jobs import the library under test from a uv environment's
    site-packages instead of the checkout; without remapping, the uploaded
    bitmap path cannot be attributed to the repository, so the library drops
    out of the coverage report while tests remain.
    """
    site_packages = _make_site_packages(tmp_path)
    (tmp_path / ".coveragerc").write_text(
        "[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n"
    )
    monkeypatch.chdir(tmp_path)
    # Default config discovery only: no active coverage instance interferes.
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: None)

    covered_lines = {
        str(tmp_path / "tests/test_x.py"): mock.Mock(to_bytes=lambda: b"abc123"),
        str(site_packages / "__init__.py"): mock.Mock(to_bytes=lambda: b"abc456"),
        "/not/repo/path/baz.py": mock.Mock(to_bytes=lambda: b"abc789"),
    }

    clear_coverage_path_caches()
    try:
        with mock.patch.object(ModuleCodeCollector, "CollectInContext") as mock_collect_in_context:
            mock_collect_in_context().__enter__().get_covered_lines.return_value = covered_lines

            with coverage_collection() as coverage_data:
                pass

            # The site-packages copy is remapped to its repository path via the
            # [paths] alias; the outside-workspace file is dropped as before.
            assert sorted(coverage_data.get_coverage_bitmaps(tmp_path)) == [
                ("/covered_pkg/__init__.py", b"abc456"),
                ("/tests/test_x.py", b"abc123"),
            ]
    finally:
        clear_coverage_path_caches()


def test_get_coverage_bitmaps_paths_alias_absolute_destination(tmp_path, monkeypatch) -> None:
    """Absolute [paths] destinations are relativized to the workspace before upload.

    A destination outside the workspace cannot be attributed to the repository,
    so the file is dropped rather than uploaded with a // prefix.
    """
    site_packages = _make_site_packages(tmp_path)
    covered_file = str(site_packages / "__init__.py")
    tests_file = str(tmp_path / "tests/test_x.py")
    monkeypatch.chdir(tmp_path)
    # Default config discovery only: no active coverage instance interferes.
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: None)

    def _covered_lines():
        return {
            tests_file: mock.Mock(to_bytes=lambda: b"abc123"),
            covered_file: mock.Mock(to_bytes=lambda: b"abc456"),
        }

    clear_coverage_path_caches()
    try:
        with mock.patch.object(ModuleCodeCollector, "CollectInContext") as mock_collect_in_context:
            mock_enter = mock_collect_in_context().__enter__()

            # Destination is absolute but inside the workspace: relativized.
            (tmp_path / ".coveragerc").write_text(
                "[paths]\ncovered_pkg =\n    %s/covered_pkg/\n    */site-packages/covered_pkg/\n" % tmp_path
            )
            mock_enter.get_covered_lines.return_value = _covered_lines()
            with coverage_collection() as coverage_data:
                pass
            assert sorted(coverage_data.get_coverage_bitmaps(tmp_path)) == [
                ("/covered_pkg/__init__.py", b"abc456"),
                ("/tests/test_x.py", b"abc123"),
            ]

            # Destination is absolute and outside the workspace: dropped.
            (tmp_path / ".coveragerc").write_text(
                "[paths]\ncovered_pkg =\n    /outside/workspace/covered_pkg/\n    */site-packages/covered_pkg/\n"
            )
            clear_coverage_path_caches()
            mock_enter.get_covered_lines.return_value = _covered_lines()
            with coverage_collection() as coverage_data:
                pass
            assert sorted(coverage_data.get_coverage_bitmaps(tmp_path)) == [
                ("/tests/test_x.py", b"abc123"),
            ]
    finally:
        clear_coverage_path_caches()


def test_get_coverage_bitmaps_paths_alias_active_coverage_instance(tmp_path, monkeypatch) -> None:
    """Aliases come from the active coverage instance when one is running.

    A pytest-cov session using a custom --cov-config keeps its aliases on the
    instance; they must be used instead of the default configuration discovery
    from the working directory, which does not see the custom config.
    """
    site_packages = _make_site_packages(tmp_path)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    # cwd has no .coveragerc; the custom config is only reachable via the instance.
    custom_config = tmp_path / "custom-coverage.cfg"
    custom_config.write_text("[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n")
    monkeypatch.chdir(cwd)

    covered_lines = {
        str(cwd / "tests/test_x.py"): mock.Mock(to_bytes=lambda: b"abc123"),
        str(site_packages / "__init__.py"): mock.Mock(to_bytes=lambda: b"abc456"),
    }

    clear_coverage_path_caches()
    try:
        cov_instance = Coverage(config_file=str(custom_config))
        with mock.patch.object(tracer_api_coverage, "get_coverage_instance", return_value=cov_instance):
            with mock.patch.object(ModuleCodeCollector, "CollectInContext") as mock_collect_in_context:
                mock_collect_in_context().__enter__().get_covered_lines.return_value = covered_lines

                with coverage_collection() as coverage_data:
                    pass

                assert sorted(coverage_data.get_coverage_bitmaps(cwd)) == [
                    ("/covered_pkg/__init__.py", b"abc456"),
                    ("/tests/test_x.py", b"abc123"),
                ]
    finally:
        clear_coverage_path_caches()


def test_coverage_path_caches_reset_for_new_session(tmp_path, monkeypatch) -> None:
    """clear_coverage_path_caches makes remapping follow a changed configuration.

    A second test session in the same interpreter must not reuse the aliases
    (or the per-path results) discovered during the first session.
    """
    site_packages = _make_site_packages(tmp_path)
    covered_file = str(site_packages / "__init__.py")
    monkeypatch.chdir(tmp_path)
    # Default config discovery only: no active coverage instance interferes.
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: None)

    def _write_config(destination: str) -> None:
        (tmp_path / ".coveragerc").write_text(
            "[paths]\ncovered_pkg =\n    %s/\n    */site-packages/covered_pkg/\n" % destination
        )

    clear_coverage_path_caches()
    try:
        _write_config("first")
        assert tracer_api_coverage._relative_coverage_path(covered_file, str(tmp_path)) == ("/first/__init__.py")

        _write_config("second")
        clear_coverage_path_caches()
        assert tracer_api_coverage._relative_coverage_path(covered_file, str(tmp_path)) == ("/second/__init__.py")
    finally:
        clear_coverage_path_caches()


def test_paths_alias_first_match_precedence(tmp_path, monkeypatch) -> None:
    """When several [paths] groups match a file, the first one wins.

    The first alias maps the file to itself; a later, broader alias must not
    override it, matching the first-match precedence the LCOV remapper uses.
    """
    tmp_path.joinpath("lib/covered_pkg").mkdir(parents=True)
    covered_file = str(tmp_path / "lib/covered_pkg/mod.py")
    # The first group's destination reproduces the executed path (an identity
    # mapping via an absolute destination); the second group would rename it.
    (tmp_path / ".coveragerc").write_text(
        "[paths]\n"
        "same =\n"
        "    %s/lib/covered_pkg/\n"
        "    */lib/covered_pkg/\n"
        "elsewhere =\n"
        "    elsewhere/\n"
        "    */covered_pkg/\n" % tmp_path
    )
    monkeypatch.chdir(tmp_path)
    # Default config discovery only: no active coverage instance interferes.
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: None)

    clear_coverage_path_caches()
    try:
        assert tracer_api_coverage._relative_coverage_path(covered_file, str(tmp_path)) == ("/lib/covered_pkg/mod.py")
    finally:
        clear_coverage_path_caches()


def test_paths_alias_relative_destination_from_subdirectory(tmp_path, monkeypatch) -> None:
    """Relative [paths] destinations are resolved against the working directory.

    Running pytest from below the repository root with a destination such as
    ../pkg/ must report the repository-relative path, not /../pkg/.
    """
    tmp_path.joinpath("lib/covered_pkg").mkdir(parents=True)
    cwd = tmp_path / "tests"
    cwd.mkdir()
    # Config discovery happens relative to the working directory.
    (cwd / ".coveragerc").write_text(
        "[paths]\ncovered_pkg =\n    ../lib/covered_pkg/\n    */site-packages/covered_pkg/\n"
    )
    site_packages = _make_site_packages(tmp_path)
    covered_file = str(site_packages / "__init__.py")
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: None)

    clear_coverage_path_caches()
    try:
        assert tracer_api_coverage._relative_coverage_path(covered_file, str(tmp_path)) == (
            "/lib/covered_pkg/__init__.py"
        )
    finally:
        clear_coverage_path_caches()


@pytest.mark.subprocess
def test_wheel_installed_library_collected_and_remapped() -> None:
    """Wheel-installed library files are collected and remapped end to end.

    Unlike the mocked tests, this goes through the real collection gates: files
    under site-packages are normally not instrumented at all, so without the
    alias matcher there would be nothing to remap.
    """
    import os
    from pathlib import Path
    import sys
    import tempfile

    repo = Path(tempfile.mkdtemp()) / "repo"
    env_site = repo / ".cache/uv-test-environments/abc/lib/python3.12/site-packages"
    (repo / "tests").mkdir(parents=True)
    (env_site / "covered_pkg").mkdir(parents=True)
    (env_site / "dep_pkg").mkdir(parents=True)
    (repo / ".coveragerc").write_text("[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n")
    (env_site / "covered_pkg/__init__.py").write_text("def bump():\n    return 42\n")
    (env_site / "dep_pkg/__init__.py").write_text("def noop():\n    pass\n")

    os.chdir(repo)

    import ddtrace.testing.internal.tracer_api.coverage as tracer_api_coverage

    tracer_api_coverage.install_coverage(repo)
    try:
        sys.path.insert(0, str(env_site))
        with tracer_api_coverage.coverage_collection() as coverage_data:
            import covered_pkg
            import dep_pkg

            assert covered_pkg.bump() == 42
            dep_pkg.noop()

        bitmaps = dict(coverage_data.get_coverage_bitmaps(relative_to=repo))
        # The aliased installed copy is collected and remapped to its repository path.
        assert "/covered_pkg/__init__.py" in bitmaps
        # A plain dependency under site-packages is still not instrumented.
        assert not any("dep_pkg" in path for path in bitmaps)
    finally:
        tracer_api_coverage.uninstall_coverage()
        sys.path.remove(str(env_site))


@pytest.mark.subprocess
def test_installed_libraries_not_collected_without_aliases() -> None:
    """Without [paths] aliases, installed libraries stay outside collection.

    The site-packages gate must hold when no alias maps the installed copy back
    to the repository: the alias matcher never admits anything.
    """
    import os
    from pathlib import Path
    import sys
    import tempfile

    repo = Path(tempfile.mkdtemp()) / "repo"
    env_site = repo / ".cache/uv-test-environments/abc/lib/python3.12/site-packages"
    (repo / "tests").mkdir(parents=True)
    (env_site / "covered_pkg").mkdir(parents=True)
    (repo / ".coveragerc").write_text("[run]\nomit = covered_pkg/vendor/*\n")
    (env_site / "covered_pkg/__init__.py").write_text("def bump():\n    return 42\n")

    os.chdir(repo)

    import ddtrace.testing.internal.tracer_api.coverage as tracer_api_coverage

    tracer_api_coverage.install_coverage(repo)
    try:
        sys.path.insert(0, str(env_site))
        with tracer_api_coverage.coverage_collection() as coverage_data:
            import covered_pkg

            assert covered_pkg.bump() == 42

        bitmaps = dict(coverage_data.get_coverage_bitmaps(relative_to=repo))
        assert not any("covered_pkg" in path for path in bitmaps)
    finally:
        tracer_api_coverage.uninstall_coverage()
        sys.path.remove(str(env_site))


def test_alias_matcher_propagates_to_spawned_processes(tmp_path, monkeypatch) -> None:
    """The alias matcher survives pickling to spawn/forkserver child collectors.

    Stowaway is what reinstalls the collector in a spawned child; without the
    matcher traveling with it, wheel-installed modules fail the child's
    site-packages gate and contribute no coverage.
    """
    import pickle

    from ddtrace.internal.coverage.multiprocessing_coverage import Stowaway

    site_packages = _make_site_packages(tmp_path)
    other_pkg = tmp_path / ".cache/env/lib/python3.12/site-packages/other_pkg"
    other_pkg.mkdir(parents=True)
    (tmp_path / ".coveragerc").write_text(
        "[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: None)

    clear_coverage_path_caches()
    try:
        stowaway = Stowaway(include_paths=[tmp_path], alias_matcher=tracer_api_coverage._AliasMatcher(tmp_path))
        state = stowaway.__getstate__()

        # The matcher round-trips through pickle and still admits installed
        # copies of the aliased library, but not other dependencies.
        matcher = pickle.loads(pickle.dumps(state["alias_matcher"]))
        assert matcher(site_packages / "__init__.py")
        assert not matcher(other_pkg / "mod.py")

        # The child-side install receives the matcher.
        with (
            mock.patch.object(ModuleCodeCollector, "install") as mock_install,
            mock.patch("ddtrace.internal.coverage.multiprocessing_coverage._patch_threading"),
            mock.patch("ddtrace.internal.coverage.multiprocessing_coverage._patch_multiprocessing"),
        ):
            Stowaway.__setstate__(Stowaway.__new__(Stowaway), state)

        mock_install.assert_called_once_with(include_paths=[tmp_path], alias_matcher=state["alias_matcher"])
    finally:
        clear_coverage_path_caches()


def test_get_coverage_bitmaps_coalesce_aliased_copies(tmp_path, monkeypatch) -> None:
    """Copies aliasing to the same upload filename yield one unioned bitmap.

    A checkout copy and a wheel-installed copy (e.g. executed in a child
    process) of the same file must not produce duplicate split entries.
    """
    from ddtrace.internal.coverage.coverage_lines import CoverageLines

    site_packages = _make_site_packages(tmp_path)
    (tmp_path / "covered_pkg").mkdir()
    (tmp_path / ".coveragerc").write_text(
        "[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: None)

    covered_lines = {
        str(tmp_path / "covered_pkg/__init__.py"): CoverageLines.from_list([1, 2]),
        str(site_packages / "__init__.py"): CoverageLines.from_list([3]),
    }

    clear_coverage_path_caches()
    try:
        with mock.patch.object(ModuleCodeCollector, "CollectInContext") as mock_collect_in_context:
            mock_collect_in_context().__enter__().get_covered_lines.return_value = covered_lines

            with coverage_collection() as coverage_data:
                pass

            assert dict(coverage_data.get_coverage_bitmaps(tmp_path)) == {
                "/covered_pkg/__init__.py": CoverageLines.from_list([1, 2, 3]).to_bytes(),
            }
    finally:
        clear_coverage_path_caches()


def test_get_coverage_bitmaps_dedupe_file_level_aliased_copies(tmp_path, monkeypatch) -> None:
    """File-level bitmaps for aliased copies are reported once per filename."""
    site_packages = _make_site_packages(tmp_path)
    (tmp_path / "covered_pkg").mkdir()
    (tmp_path / ".coveragerc").write_text(
        "[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: None)

    clear_coverage_path_caches()
    try:
        with (
            mock.patch.object(ModuleCodeCollector, "CollectInContext") as mock_collect_in_context,
            mock.patch.object(ModuleCodeCollector, "file_level_coverage_enabled", return_value=True),
        ):
            mock_collect_in_context().__enter__().get_covered_file_paths.return_value = [
                str(tmp_path / "covered_pkg/__init__.py"),
                str(site_packages / "__init__.py"),
            ]

            with coverage_collection() as coverage_data:
                pass

            assert list(coverage_data.get_coverage_bitmaps(tmp_path)) == [("/covered_pkg/__init__.py", mock.ANY)]
    finally:
        clear_coverage_path_caches()


@pytest.mark.subprocess
def test_venv_installed_library_collected_and_remapped() -> None:
    """A library installed in the interpreter's own site-packages is collected.

    This is the normal wheel-under-test scenario: the wheel lives in the active
    virtualenv, which the collector excludes as interpreter paths (purelib/
    platlib). The alias match must be exempt from those exclusions, while plain
    venv dependencies stay excluded.
    """
    import os
    from pathlib import Path
    import shutil
    import sysconfig
    import tempfile

    repo = Path(tempfile.mkdtemp()) / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / ".coveragerc").write_text("[paths]\nvenvlib =\n    venvlib/\n    */site-packages/venvlib/\n")

    # Install the library under test (and a dependency) in the interpreter's
    # real site-packages, not a synthetic directory.
    purelib = Path(sysconfig.get_paths()["purelib"])
    lib_dir = purelib / "venvlib"
    dep_dir = purelib / "venvdep"
    # A previous failed run may not have cleaned up its directories.
    lib_dir.mkdir(parents=True, exist_ok=True)
    dep_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "__init__.py").write_text("def bump():\n    return 42\n")
    (dep_dir / "__init__.py").write_text("def noop():\n    pass\n")

    # The import system may have cached the site-packages listing before the
    # packages above were created (directory mtime granularity).
    import importlib

    importlib.invalidate_caches()

    os.chdir(repo)

    import ddtrace.testing.internal.tracer_api.coverage as tracer_api_coverage

    tracer_api_coverage.install_coverage(repo)
    try:
        with tracer_api_coverage.coverage_collection() as coverage_data:
            import venvdep
            import venvlib

            assert venvlib.bump() == 42
            venvdep.noop()

        bitmaps = dict(coverage_data.get_coverage_bitmaps(relative_to=repo))
        # The aliased wheel copy is collected and remapped to its repository path.
        assert "/venvlib/__init__.py" in bitmaps
        # A plain dependency from the same site-packages is still excluded.
        assert not any("venvdep" in path for path in bitmaps)
    finally:
        tracer_api_coverage.uninstall_coverage()
        shutil.rmtree(lib_dir, ignore_errors=True)
        shutil.rmtree(dep_dir, ignore_errors=True)


def test_coverage_path_aliases_refresh_on_instance_start(tmp_path, monkeypatch) -> None:
    """Aliases rebuild when the active coverage instance changes.

    A module imported during early conftest loading resolves aliases before
    pytest-cov starts its custom-configured instance; the later instance's
    aliases must then be honored without an explicit cache clear.
    """
    site_packages = _make_site_packages(tmp_path)
    covered_file = str(site_packages / "__init__.py")
    # No .coveragerc in the workspace: default discovery finds no aliases.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: None)

    custom_config = tmp_path / "custom-coverage.cfg"
    custom_config.write_text("[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n")

    clear_coverage_path_caches()
    try:
        # Before the instance starts there are no aliases: the file keeps the
        # workspace-relative site-packages path.
        assert tracer_api_coverage._relative_coverage_path(covered_file, str(tmp_path)) != ("/covered_pkg/__init__.py")

        cov_instance = Coverage(config_file=str(custom_config))
        with mock.patch.object(tracer_api_coverage, "get_coverage_instance", return_value=cov_instance):
            # No explicit clear: the newly active instance's aliases apply.
            assert tracer_api_coverage._relative_coverage_path(covered_file, str(tmp_path)) == (
                "/covered_pkg/__init__.py"
            )
    finally:
        clear_coverage_path_caches()


def test_alias_matcher_carries_resolved_aliases_to_children(tmp_path, monkeypatch) -> None:
    """Pickling snapshots the resolved aliases for spawned child processes.

    A child process has no parent coverage instance, so configuration
    discovery there cannot see a custom --cov-config's aliases.
    """
    import pickle

    # cwd without a .coveragerc: default discovery in a child would find nothing.
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    custom_config = tmp_path / "custom-coverage.cfg"
    custom_config.write_text("[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n")
    site_packages = _make_site_packages(tmp_path)
    cov_instance = Coverage(config_file=str(custom_config))
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: cov_instance)

    clear_coverage_path_caches()
    try:
        matcher = tracer_api_coverage._AliasMatcher(tmp_path)
        child_matcher = pickle.loads(pickle.dumps(matcher))

        # In the child there is no active instance and no default config.
        with mock.patch.object(tracer_api_coverage, "get_coverage_instance", lambda: None):
            assert child_matcher(site_packages / "__init__.py")
    finally:
        clear_coverage_path_caches()


def test_alias_matcher_rejects_identity_aliases(tmp_path, monkeypatch) -> None:
    """Broad aliases that map a file to where it already sits do not admit it.

    A whole-workspace alias (e.g. the repository's own "source" group) maps
    every nested dependency to itself; those must stay excluded so only
    relocated installed copies of the library under test are collected.
    """
    site_packages = _make_site_packages(tmp_path)
    dep_venv = tmp_path / ".venv/lib/python3.12/site-packages/dep_pkg"
    dep_venv.mkdir(parents=True)
    lib_file = site_packages / "__init__.py"
    dep_file = dep_venv / "mod.py"

    def _write_config(broad_first: bool) -> None:
        covered = "[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n"
        source = f"source =\n    ./\n    {tmp_path}\n"
        (tmp_path / ".coveragerc").write_text(source + covered if broad_first else covered + source)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: None)
    matcher = tracer_api_coverage._AliasMatcher(tmp_path)

    clear_coverage_path_caches()
    try:
        _write_config(broad_first=False)
        # The library copy is relocated to its repository path: admitted.
        assert matcher(lib_file)
        # The dependency maps to the location it already occupies: excluded.
        assert not matcher(dep_file)

        # First-match precedence applies to admission as well: with the broad
        # alias listed first, the library copy is identity-mapped and excluded.
        _write_config(broad_first=True)
        clear_coverage_path_caches()
        assert not matcher(lib_file)
    finally:
        clear_coverage_path_caches()


def test_alias_relative_destination_uses_config_base_dir(tmp_path, monkeypatch) -> None:
    """Relative alias destinations resolve against the config's directory.

    A test that changes the working directory before importing the installed
    library must not make the destination resolve under the temporary
    directory.
    """
    site_packages = _make_site_packages(tmp_path)
    (tmp_path / ".coveragerc").write_text(
        "[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: None)

    clear_coverage_path_caches()
    try:
        # Aliases are resolved while the configuration is discoverable.
        assert tracer_api_coverage._relative_coverage_path(str(site_packages / "__init__.py"), str(tmp_path)) == (
            "/covered_pkg/__init__.py"
        )

        # A later working-directory change must not change the resolution for
        # files resolved after the change.
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        assert tracer_api_coverage._relative_coverage_path(str(site_packages / "mod.py"), str(tmp_path)) == (
            "/covered_pkg/mod.py"
        )
    finally:
        clear_coverage_path_caches()


@pytest.mark.subprocess
def test_early_imported_library_reinstrumented_on_instance_start() -> None:
    """A library imported before a custom-config instance starts is revisited.

    The collector installs while conftest files load, before a pytest-cov
    --cov-config instance starts in pytest_configure. Modules imported in
    between are rejected at import; the alias change must re-instrument them
    in time for test execution.
    """
    import os
    from pathlib import Path
    import sys
    import tempfile
    from unittest import mock

    repo = Path(tempfile.mkdtemp()) / "repo"
    env_site = repo / ".cache/uv-test-environments/abc/lib/python3.12/site-packages"
    (repo / "tests").mkdir(parents=True)
    (env_site / "early_pkg").mkdir(parents=True)
    (env_site / "dep_pkg").mkdir(parents=True)
    (env_site / "dep_pkg/__init__.py").write_text("def noop():\n    pass\n")
    (env_site / "early_pkg/__init__.py").write_text(
        "from dep_pkg import noop as dep_noop\n"
        "\n"
        "\n"
        "def bump():\n"
        "    return 42\n"
        "\n"
        "\n"
        "class Thing:\n"
        "    @staticmethod\n"
        "    def smeth():\n"
        "        return 1\n"
        "\n"
        "    @property\n"
        "    def prop(self):\n"
        "        return 2\n"
    )

    os.chdir(repo)

    import ddtrace.testing.internal.tracer_api.coverage as tracer_api_coverage

    # The aliases only exist on a custom-configured coverage instance that has
    # not started yet (no .coveragerc in the workspace).
    custom_config = repo / "custom-coverage.cfg"
    custom_config.write_text("[paths]\nearly_pkg =\n    early_pkg/\n    */site-packages/early_pkg/\n")

    tracer_api_coverage.install_coverage(repo)
    try:
        sys.path.insert(0, str(env_site))

        # The module is imported before the custom instance starts: no aliases
        # are discoverable, so it is rejected at import time.
        from coverage import Coverage
        import early_pkg

        cov_instance = Coverage(config_file=str(custom_config))
        with mock.patch.object(tracer_api_coverage, "get_coverage_instance", return_value=cov_instance):
            # Entering the collection context refreshes the aliases (as at the
            # start of each test) and re-instruments the early-imported module.
            with tracer_api_coverage.coverage_collection() as coverage_data:
                assert early_pkg.bump() == 42
                assert early_pkg.Thing.smeth() == 1
                assert early_pkg.Thing().prop == 2
                early_pkg.dep_noop()
            bitmaps = dict(coverage_data.get_coverage_bitmaps(relative_to=repo))

        assert "/early_pkg/__init__.py" in bitmaps
        # The re-exported dependency stays excluded.
        assert not any("dep_pkg" in path for path in bitmaps)
    finally:
        tracer_api_coverage.uninstall_coverage()
        sys.path.remove(str(env_site))


def test_alias_matcher_preserves_empty_alias_snapshot(tmp_path, monkeypatch) -> None:
    """An empty alias snapshot stays empty in spawned children.

    A custom --cov-config without [paths] snapshots an empty tuple; a child
    process whose working directory holds an alias-bearing default config must
    not rediscover it and admit packages the parent excluded.
    """
    import pickle

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    # Default discovery in the child would find this...
    (cwd / ".coveragerc").write_text("[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n")
    site_packages = _make_site_packages(tmp_path)

    # ...but the parent's active instance has no aliases at all.
    custom_config = tmp_path / "custom-coverage.cfg"
    custom_config.write_text("[run]\nomit = nothing\n")
    monkeypatch.chdir(cwd)
    cov_instance = Coverage(config_file=str(custom_config))
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: cov_instance)

    clear_coverage_path_caches()
    try:
        matcher = tracer_api_coverage._AliasMatcher(tmp_path)
        child_matcher = pickle.loads(pickle.dumps(matcher))

        assert not child_matcher(site_packages / "__init__.py")
    finally:
        clear_coverage_path_caches()


def test_alias_matcher_preserves_snapshot_across_nested_spawns(tmp_path, monkeypatch) -> None:
    """A restored matcher keeps its inherited aliases when re-pickled.

    A spawned worker launching another spawn/forkserver process must not
    rebuild aliases from its local configuration; the grandchild inherits the
    original snapshot.
    """
    import pickle

    cwd = tmp_path / "cwd"
    cwd.mkdir()  # no .coveragerc here
    custom_config = tmp_path / "custom-coverage.cfg"
    custom_config.write_text("[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n")
    site_packages = _make_site_packages(tmp_path)
    monkeypatch.chdir(cwd)
    cov_instance = Coverage(config_file=str(custom_config))
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: cov_instance)

    clear_coverage_path_caches()
    try:
        matcher = tracer_api_coverage._AliasMatcher(tmp_path)
        child_matcher = pickle.loads(pickle.dumps(matcher))

        # The grandchild is pickled from the restored matcher with no active
        # instance and no discoverable configuration.
        with mock.patch.object(tracer_api_coverage, "get_coverage_instance", lambda: None):
            grandchild_matcher = pickle.loads(pickle.dumps(child_matcher))

        assert grandchild_matcher(site_packages / "__init__.py")
    finally:
        clear_coverage_path_caches()


def test_absolute_alias_destination_normalized(tmp_path, monkeypatch) -> None:
    """Absolute destinations with '..' normalize before containment checks."""
    site_packages = _make_site_packages(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tracer_api_coverage, "get_coverage_instance", lambda: None)

    clear_coverage_path_caches()
    try:
        # A redundant '..' segment inside the workspace.
        (tmp_path / ".coveragerc").write_text(
            "[paths]\ncovered_pkg =\n    %s/src/../covered_pkg/\n    */site-packages/covered_pkg/\n" % tmp_path
        )
        assert tracer_api_coverage._relative_coverage_path(str(site_packages / "__init__.py"), str(tmp_path)) == (
            "/covered_pkg/__init__.py"
        )

        # A destination escaping the workspace via '..': dropped.
        (tmp_path / ".coveragerc").write_text(
            "[paths]\ncovered_pkg =\n    %s/../outside/covered_pkg/\n    */site-packages/covered_pkg/\n" % tmp_path
        )
        clear_coverage_path_caches()
        assert tracer_api_coverage._relative_coverage_path(str(site_packages / "__init__.py"), str(tmp_path)) is None
    finally:
        clear_coverage_path_caches()
