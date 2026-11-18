from pathlib import Path
from unittest import mock

from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.testing.internal.tracer_api.coverage import coverage_collection


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


def test_get_covered_files_from_line_coverage_does_not_encode_bitmaps() -> None:
    with (
        mock.patch.object(ModuleCodeCollector, "CollectInContext") as collect,
        mock.patch.object(ModuleCodeCollector, "file_level_coverage_enabled", return_value=False),
    ):
        line_data = mock.Mock()
        collect().__enter__().get_covered_lines.return_value = {
            "/repo/path/module.py": line_data,
            "/outside/module.py": line_data,
        }
        with coverage_collection() as data:
            pass
        assert list(data.get_covered_files(Path("/repo/path"))) == ["/module.py"]
        line_data.to_bytes.assert_not_called()


def test_get_covered_files_from_file_coverage() -> None:
    with (
        mock.patch.object(ModuleCodeCollector, "CollectInContext") as collect,
        mock.patch.object(ModuleCodeCollector, "file_level_coverage_enabled", return_value=True),
    ):
        collect().__enter__().get_covered_file_paths.return_value = {
            "/repo/path/module.py",
            "/outside/module.py",
        }
        with coverage_collection() as data:
            pass
        assert set(data.get_covered_files(Path("/repo/path"))) == {"/module.py"}
        collect().__enter__().get_covered_lines.assert_not_called()
