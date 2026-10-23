from pathlib import Path
from unittest import mock

from ddtrace.internal.coverage.code import ModuleCodeCollector
import ddtrace.testing.internal.tracer_api.coverage as tracer_api_coverage
from ddtrace.testing.internal.tracer_api.coverage import coverage_collection


def _clear_coverage_path_caches() -> None:
    tracer_api_coverage._coverage_path_aliases.cache_clear()
    tracer_api_coverage._relative_coverage_path.cache_clear()


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
    site_packages = tmp_path / ".cache/uv-test-environments/abc/lib/python3.12/site-packages/covered_pkg"
    site_packages.mkdir(parents=True)
    (tmp_path / ".coveragerc").write_text(
        "[paths]\ncovered_pkg =\n    covered_pkg/\n    */site-packages/covered_pkg/\n"
    )
    monkeypatch.chdir(tmp_path)

    covered_lines = {
        str(tmp_path / "tests/test_x.py"): mock.Mock(to_bytes=lambda: b"abc123"),
        str(site_packages / "__init__.py"): mock.Mock(to_bytes=lambda: b"abc456"),
        "/not/repo/path/baz.py": mock.Mock(to_bytes=lambda: b"abc789"),
    }

    _clear_coverage_path_caches()
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
        _clear_coverage_path_caches()
