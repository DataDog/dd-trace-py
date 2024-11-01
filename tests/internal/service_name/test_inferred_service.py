import pytest
import os
import pathlib
import tempfile
from unittest.mock import patch
from ddtrace.settings._inferred_service import detect_service

@pytest.fixture
def mock_file_system():
    """Setup a mock filesystem."""
    # Use a temporary directory for testing.
    with tempfile.TemporaryDirectory() as temp_dir:
        base_path = pathlib.Path(temp_dir)
        base_path.mkdir(exist_ok=True)

        # Create the mock directory structure
        (base_path / "modules" / "m1" / "first" / "nice" / "package").mkdir(parents=True)
        (base_path / "modules" / "m2").mkdir(parents=True)
        (base_path / "apps" / "app1").mkdir(parents=True)
        (base_path / "apps" / "app2" / "cmd").mkdir(parents=True)

        # Create __init__.py files and other Python files
        (base_path / "modules" / "m1" / "first" / "nice" / "package" / "__init__.py").touch()
        (base_path / "modules" / "m1" / "first" / "nice" / "__init__.py").touch()
        (base_path / "modules" / "m1" / "first" / "nice" / "something.py").touch()
        (base_path / "modules" / "m1" / "first" / "__init__.py").touch()
        (base_path / "modules" / "m1" / "__init__.py").touch()
        (base_path / "apps" / "app1" / "__main__.py").touch()
        (base_path / "apps" / "app2" / "cmd" / "run.py").touch()
        (base_path / "apps" / "app2" / "setup.py").touch()

        yield base_path

def test_python_detector(mock_file_system):
    # Mock the current working directory to the test_modules path
    with patch("os.getcwd", return_value=str(mock_file_system)):
        tests = [
            ("python modules/m1/first/nice/package", "m1.first.nice.package"),
            ("python modules/m1/first/nice", "m1.first.nice"),
            ("python modules/m1/first/nice/something.py", "m1.first.nice"),
            ("python modules/m1/first", "m1.first"),
            ("python modules/m2", "m2"),
            ("python apps/app1", "app1"),
            ("python apps/app2/cmd/run.py", "app2"),
            ("python apps/app2/setup.py", "app2"),
        ]

        for cmd, expected in tests:
            cmd_parts = cmd.split(" ")
            detected_name = detect_service(cmd_parts)
            assert detected_name == expected
