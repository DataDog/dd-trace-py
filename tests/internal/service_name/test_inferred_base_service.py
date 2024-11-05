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

        # Additional edge cases
        (base_path / "modules" / "no" / "python_files" / "here.txt").touch()  # Module with no subdirectories
        (base_path / "modules" / "m1" / "first" / "nice" / "package" / "not_a_python_file.txt").touch()

        yield base_path


def test_python_detector(mock_file_system):
    # Mock the current working directory to the test_modules path
    with patch("os.getcwd", return_value=str(mock_file_system)):
        tests = [
            # # SSI Test Cases copied over from Injection Code
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
            # Gunicorn Test cases
            ("venv/bin/python3.11/ddtrace-run gunicorn -w 4 -b 0.0.0.0:8000 app:app", "app"),
            ("venv/bin/python3.11/ddtrace-run venv/bin/gunicorn -w 4 -b 0.0.0.0:8000 app:app", "app"),
            ("venv/bin/gunicorn -w 4 -b 0.0.0.0:8000 app:app", "app"),
            ("gunicorn -w 4 -b 0.0.0.0:8000 app:app", "app"),
            ("gunicorn -w 4 -b 127.0.0.1:8000 wsgi_app:app", "wsgi_app"),
            ("gunicorn -w 4 wsgi_app:app", "wsgi_app"),
            ("gunicorn -b '0.0.0.0:8000' flask_app:app", "flask_app"),
            (
                "gunicorn -w 2 -b 0.0.0.0:8000 modules/m1/first/nice/package:app",
                "modules/m1/first/nice/package",
            ),  # NOTE: is this what we want or do we want the module name using "." separators?,
            ("gunicorn apps.app1:app", "apps.app1"),
            ("gunicorn -w 4 apps.app2:app", "apps.app2"),
            # Edge Cases: Different Python commands
            # ("flask run --app apps/app3/app.py", "app3"), # TODO: Cover Flask case
            # ("uwsgi --http :8000 --wsgi-file apps/app1/__main__.py", "app1"), # TODO: Cover uwsgi case
            # ("hypercorn apps.app1:app", "app1"), # TODO: Cover hypercorn case
        ]

        for cmd, expected in tests:
            cmd_parts = cmd.split(" ")
            detected_name = detect_service(cmd_parts)
            assert detected_name == expected, f"Test failed for command: [{cmd}]"
