from pathlib import Path
from unittest import mock

from ddtestpy.internal.coverage_api import coverage_collection
from ddtestpy.vendor.ddtrace_coverage.code import ModuleCodeCollector


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
