from pathlib import Path
from unittest import mock

from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.testing.internal.tracer_api.coverage import coverage_collection
from ddtrace.testing.internal.tracer_api.coverage import install_coverage


def test_install_coverage_passes_file_level_mode() -> None:
    with (
        mock.patch("ddtrace.internal.coverage.installer.install") as mock_install,
        mock.patch.object(ModuleCodeCollector, "start_coverage") as mock_start_coverage,
    ):
        install_coverage(Path("/repo/path"), file_level_coverage=True)

    mock_install.assert_called_once_with(
        include_paths=[Path("/repo/path")],
        collect_import_time_coverage=True,
        file_level_coverage=True,
    )
    mock_start_coverage.assert_called_once_with()


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
