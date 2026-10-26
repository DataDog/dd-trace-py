from pathlib import Path
from unittest import mock

from coverage import Coverage

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
