import pathlib
import tempfile
from unittest.mock import patch

import pytest

from ddtrace.settings._inferred_base_service import detect_service


@pytest.fixture
def mock_file_system():
    """Setup a mock filesystem."""
    # Use a temporary directory for testing.
    with tempfile.TemporaryDirectory() as temp_dir:
        base_path = pathlib.Path(temp_dir)
        base_path.mkdir(exist_ok=True)

        # Create the mock directory structure
        (base_path / "__pycache__").mkdir(parents=True)
        (base_path / "venv" / "bin" / "python3.11").mkdir(parents=True)
        (base_path / "venv" / "bin" / "gunicorn").mkdir(parents=True)

        # add a test dir
        (base_path / "tests" / "contrib" / "aiohttp").mkdir(parents=True)

        (base_path / "modules" / "m1" / "first" / "nice" / "package").mkdir(parents=True)
        (base_path / "modules" / "m2").mkdir(parents=True)
        (base_path / "modules" / "no" / "python_files").mkdir(parents=True)
        (base_path / "apps" / "app1").mkdir(parents=True)
        (base_path / "apps" / "app2" / "cmd").mkdir(parents=True)

        # Create Python and other files
        (base_path / "__pycache__" / "app.cpython-311.pyc").touch()
        (base_path / "venv" / "bin" / "python3.11" / "ddtrace" / "__init__.py").mkdir(parents=True)
        (base_path / "venv" / "bin" / "python3.11" / "gunicorn" / "__init__.py").mkdir(parents=True)
        (base_path / "venv" / "bin" / "gunicorn" / "__init__.py").touch()

        (base_path / "modules" / "m1" / "first" / "nice" / "package" / "__init__.py").touch()
        (base_path / "modules" / "m1" / "first" / "nice" / "__init__.py").touch()
        (base_path / "modules" / "m1" / "first" / "nice" / "something.py").touch()
        (base_path / "modules" / "m1" / "first" / "__init__.py").touch()
        (base_path / "modules" / "m1" / "__init__.py").touch()
        (base_path / "apps" / "app1" / "__main__.py").touch()
        (base_path / "apps" / "app2" / "cmd" / "run.py").touch()
        (base_path / "apps" / "app2" / "setup.py").touch()

        (base_path / "tests" / "contrib" / "aiohttp" / "test.py").touch()
        (base_path / "tests" / "contrib" / "aiohttp" / "__init__.py").touch()
        (base_path / "tests" / "contrib" / "__init__.py").touch()
        (base_path / "tests" / "__init__.py").touch()

        # Additional edge cases
        (base_path / "modules" / "no" / "python_files" / "here.txt").touch()  # Module with no subdirectories
        (base_path / "modules" / "m1" / "first" / "nice" / "package" / "not_a_python_file.txt").touch()

        yield base_path


@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("python modules/m1/first/nice/package", "m1.first.nice.package"),
        ("python modules/m1/first/nice", "m1.first.nice"),
        ("python modules/m1/first/nice/something.py", "m1.first.nice"),
        ("python modules/m1/first", "m1.first"),
        ("python modules/m2", "m2"),
        ("python apps/app1", "app1"),
        ("python apps/app2/cmd/run.py", "app2"),
        ("python apps/app2/setup.py", "app2"),
        ("DD_ENV=prod OTHER_ENV_VAR=hi python apps/app2/setup.py", "app2"),
        ("python3.7 apps/app2/setup.py", "app2"),
        ("/usr/bin/python3.11 apps/app2/setup.py", "app2"),
        # Additional Python test cases
        ("venv/bin/python3.11/ddtrace-run venv/bin/python3.11 apps/app2/setup.py", "app2"),
        ("venv/bin/python3.11/ddtrace-run python apps/app2/setup.py", "app2"),
        ("ddtrace-run python apps/app2/setup.py", "app2"),
        ("python3.12 apps/app2/cmd/run.py", "app2"),
        ("python -m m1.first.nice.package", "m1.first.nice.package"),
        ("python -m http.server 8000", "http.server"),
        # pytest
        ("pytest tests/contrib/aiohttp", "tests.contrib.aiohttp"),
        ("pytest --ddtrace tests/contrib/aiohttp", "tests.contrib.aiohttp"),
    ],
)
def test_python_detector(cmd, expected, mock_file_system):
    # Mock the current working directory to the test_modules path
    with patch("os.getcwd", return_value=str(mock_file_system)):
        cmd_parts = cmd.split(" ")
        detected_name = detect_service(cmd_parts)

        assert detected_name == expected, f"Test failed for command: [{cmd}]"
