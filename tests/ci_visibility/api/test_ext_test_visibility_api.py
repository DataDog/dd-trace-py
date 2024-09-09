from os import getcwd as os_getcwd
from pathlib import Path

import pytest

from ddtrace.ext.test_visibility.api import TestSourceFileInfo


class TestCISourceFileInfo:
    def test_source_file_info_happy_path(self):
        cisi = TestSourceFileInfo(Path("/absolute/path/to/my_file_name"), 1, 2)
        assert cisi.path.is_absolute()
        assert cisi.path == Path("/absolute/path/to/my_file_name")

    def test_source_file_info_makes_path_absolute(self):
        """Should fail if the path is a string"""
        cisi = TestSourceFileInfo(Path("my_file_name"), 3, 4)
        expected_path = Path(os_getcwd()) / "my_file_name"
        assert cisi.path.is_absolute()
        assert cisi.path == expected_path

    def test_source_file_info_enforces_path_type(self):
        with pytest.raises(ValueError):
            _ = TestSourceFileInfo("my_file_name", 5, 6)

    def test_source_file_info_path_must_be_set(self):
        with pytest.raises(ValueError):
            _ = TestSourceFileInfo(None, 5, 6)
        with pytest.raises(TypeError):
            _ = TestSourceFileInfo(start_line=5, end_line=6)

    def test_source_file_info_enforces_lines_are_ints(self):
        with pytest.raises(ValueError):
            _ = TestSourceFileInfo(Path("/absolute/path/my_file_name"), "5", 6)
        with pytest.raises(ValueError):
            _ = TestSourceFileInfo(Path("/absolute/path/my_file_name"), 5, "6")
        with pytest.raises(ValueError):
            _ = TestSourceFileInfo(Path("/absolute/path/my_file_name"), "5", "6")

    def test_source_file_info_enforces_lines_are_positive(self):
        with pytest.raises(ValueError):
            _ = TestSourceFileInfo(Path("/absolute/path/my_file_name"), -1, 1)
        with pytest.raises(ValueError):
            _ = TestSourceFileInfo(Path("/absolute/path/my_file_name"), 1, -1)
        with pytest.raises(ValueError):
            _ = TestSourceFileInfo(Path("/absolute/path/my_file_name"), -1, -1)

    def test_source_file_info_enforces_start_line_less_than_end_line(self):
        with pytest.raises(ValueError):
            _ = TestSourceFileInfo(Path("/absolute/path/my_file_name"), 2, 1)
        with pytest.raises(ValueError):
            _ = TestSourceFileInfo(end_line=1, start_line=2, path=Path("/absolute/path/my_file_name"))
        with pytest.raises(ValueError):
            # start_line cannot be None if end_line is provided
            _ = TestSourceFileInfo(Path("/absolute/path/my_file_name"), end_line=1)
