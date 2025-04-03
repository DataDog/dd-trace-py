import os
import pathlib
import shlex
import subprocess
import tempfile
import textwrap
from unittest.mock import patch

import pytest

from ddtrace.settings._inferred_base_service import _module_exists
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
        (base_path / "tests" / "contrib" / "aiohttp" / "app").mkdir(parents=True)

        # other cases

        (base_path / "modules" / "m1" / "first" / "nice" / "package").mkdir(parents=True)
        (base_path / "modules" / "m2").mkdir(parents=True)
        (base_path / "modules" / "no" / "python_files").mkdir(parents=True)
        (base_path / "apps" / "app1").mkdir(parents=True)
        (base_path / "apps" / "app2" / "cmd").mkdir(parents=True)
        (base_path / "app" / "app2" / "cmd").mkdir(parents=True)

        # Create Python and other files
        (base_path / "__pycache__" / "app.cpython-311.pyc").touch()
        (base_path / "venv" / "bin" / "python3.11" / "ddtrace" / "__init__.py").mkdir(parents=True)
        (base_path / "venv" / "bin" / "python3.11" / "gunicorn" / "__init__.py").mkdir(parents=True)
        (base_path / "venv" / "bin" / "gunicorn" / "__init__.py").touch()

        # Create `__init__.py` files that indicate packages
        (base_path / "modules" / "m1" / "first" / "nice" / "package" / "__init__.py").touch()
        (base_path / "modules" / "m1" / "first" / "nice" / "__init__.py").touch()
        (base_path / "modules" / "m1" / "first" / "nice" / "app.py").touch()
        (base_path / "modules" / "m1" / "first" / "__init__.py").touch()
        (base_path / "modules" / "m1" / "__init__.py").touch()
        (base_path / "modules" / "m2" / "__init__.py").touch()
        (base_path / "apps" / "app1" / "__main__.py").touch()
        (base_path / "apps" / "app2" / "cmd" / "run.py").touch()
        (base_path / "apps" / "app2" / "setup.py").touch()

        (base_path / "tests" / "contrib" / "aiohttp" / "app" / "web.py").touch()
        (base_path / "tests" / "contrib" / "aiohttp" / "app" / "__init__.py").touch()
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
        ("python tests/contrib/aiohttp/app/web.py", "tests.contrib.aiohttp.app"),
        ("python tests/contrib/aiohttp", "tests.contrib.aiohttp"),
        ("python tests/contrib", "tests.contrib"),
        ("python tests", "tests"),
        ("python modules/m1/first/nice/package", "m1.first.nice.package"),
        ("python modules/m1/first/nice", "m1.first.nice"),
        ("python modules/m1/first/nice/app.py", "m1.first.nice"),
        ("python modules/m1/first", "m1.first"),
        ("python modules/m2", "m2"),
        ("python apps/app1", "app1"),
        ("python apps/app2/cmd/run.py", "app2"),
        ("python apps/app2/setup.py", "app2"),
        ("DD_ENV=prod OTHER_ENV_VAR=hi python apps/app2/setup.py", "app2"),
        ("python3.8 apps/app2/setup.py", "app2"),
        ("/usr/bin/python3.11 apps/app2/setup.py", "app2"),
        # Additional Python test cases
        ("venv/bin/python3.11/ddtrace-run venv/bin/python3.11 apps/app2/setup.py", "app2"),
        ("venv/bin/python3.11/ddtrace-run python apps/app2/setup.py", "app2"),
        ("ddtrace-run python apps/app2/setup.py", "app2"),
        ("python3.12 apps/app2/cmd/run.py", "app2"),
        ("python -m tests.contrib.aiohttp.app.web", "tests.contrib.aiohttp.app.web"),
        ("python -m tests.contrib.aiohttp.app", "tests.contrib.aiohttp.app"),
        ("python -m tests.contrib.aiohttp", "tests.contrib.aiohttp"),
        ("python -m tests.contrib", "tests.contrib"),
        ("python -m tests", "tests"),
        ("python -m http.server 8000", "http.server"),
        ("python --some-flag apps/app1", "app1"),
        # pytest
        ("pytest tests/contrib/aiohttp", "tests.contrib.aiohttp"),
        ("pytest --ddtrace tests/contrib/aiohttp", "tests.contrib.aiohttp"),
        ("pytest --no-cov tests/contrib/aiohttp", "tests.contrib.aiohttp"),
    ],
)
def test_python_detector_service_name_should_exist_file_exists(cmd, expected, mock_file_system):
    # Mock the current working directory to the test_modules path
    with patch("os.getcwd", return_value=str(mock_file_system)):
        cmd_parts = shlex.split(cmd)
        detected_name = detect_service(cmd_parts)

        assert detected_name == expected, f"Test failed for command: [{cmd}]"


@pytest.mark.parametrize(
    "cmd,expected",
    [
        # Commands that should not produce a service name
        ("", None),  # Empty command
        ("python non_existing_file.py", None),  # Non-existing Python script
        ("python invalid_script.py", None),  # Invalid script that isn't found
        ("gunicorn app:app", None),  # Non-Python command
        ("ls -la", None),  # Non-Python random command
        ("cat README.md", None),  # Another random command
        ("python -m non_existing_module", None),  # Non-existing Python module
        ("python -c 'print([])'", None),  # Python inline code not producing a service
        ("python -m -c 'print([]])'", None),  # Inline code with module flag
        ("echo 'Hello, World!'", None),  # Not a Python service
        ("python3.11 /path/to/some/non_python_file.txt", None),  # Non-Python file
        ("/usr/bin/ls", None),  # Another system command
        ("some_executable --ddtrace hello", None),
        ("python version", None),
        ("python -m -v --hello=maam", None),
        # error produced from a test, ensure an arg that is very long doesn't break stuff
        (
            "ddtrace-run pytest -k 'not test_reloader and not test_reload_listeners and not "
            + "test_no_exceptions_when_cancel_pending_request and not test_add_signal and not "
            + "test_ode_removes and not test_skip_touchup and not test_dispatch_signal_triggers"
            + " and not test_keep_alive_connection_context and not test_redirect_with_params and"
            + " not test_keep_alive_client_timeout and not test_logger_vhosts and not test_ssl_in_multiprocess_mode'",
            None,
        ),
    ],
)
def test_no_service_name(cmd, expected, mock_file_system):
    with patch("os.getcwd", return_value=str(mock_file_system)):
        cmd_parts = shlex.split(cmd)
        detected_name = detect_service(cmd_parts)

        assert detected_name == expected, f"Test failed for command: [{cmd}]"


@pytest.mark.parametrize(
    "cmd,expected",
    [
        # Command that is too long
        ("python " + " ".join(["arg"] * 1000), None),  # Excessively long command
        # Path with special characters
        (r"python /path/with/special/characters/!@#$%^&*()_/some_script.py", None),  # Special characters
        # Path too deep
        (f"python {'/'.join(['deep' * 50])}/script.py", None),  # Excessively deep path
    ],
)
def test_chaos(cmd, expected, mock_file_system):
    with patch("os.getcwd", return_value=str(mock_file_system)):
        cmd_parts = shlex.split(cmd)
        detected_name = detect_service(cmd_parts)

        assert detected_name == expected, f"Chaos test failed for command: [{cmd}]"


@pytest.mark.parametrize(
    "module_name,should_exist",
    [
        ("tests.contrib.aiohttp.app.web", True),
        ("tests.contrib.aiohttp.app", True),
        ("tests.contrib.aiohttp", True),
        ("tests.contrib", True),
        ("tests", True),
        ("tests.releasenotes", False),
        ("non_existing_module", False),
    ],
)
def test_module_exists(module_name, should_exist):
    exists = _module_exists(module_name)

    assert exists == should_exist, f"Module {module_name} existence check failed."


@pytest.mark.parametrize(
    "cmd,default,expected",
    [
        ("ddtrace-run python app/web.py", None, "app"),
        ("ddtrace-run python app/web.py", "test-app", "test-app"),
        ("DD_SERVICE=app ddtrace-run python app/web.py", None, "app"),
        ("DD_SERVICE=app ddtrace-run python app/web.py", "test-appp", "app"),
    ],
)
def test_get_service(cmd, default, expected, testdir):
    cmd_parts = shlex.split(cmd)

    env = os.environ.copy()
    # Extract environment variables from the command (e.g., DD_SERVICE=app)
    env_vars = {}
    command = []
    for part in cmd_parts:
        if "=" in part and not part.startswith(("'", '"')):
            key, value = part.split("=", 1)
            env_vars[key] = value
        else:
            command.append(part)
    env.update(env_vars)

    app_dir = testdir.mkdir("app")

    web_py_content = textwrap.dedent(
        f"""
        from ddtrace import config

        # Retrieve the service name using the _get_service method
        service = config._get_service(default={repr(default)})

        # Assert that the detected service name matches the expected value
        assert service == {repr(expected)}, f"Expected service '{repr(expected)}', but got '{{service}}'"
    """
    )

    # Create the web.py file within the app directory
    app_dir.join("web.py").write(web_py_content)
    app_dir.join("__init__.py").write("# Init for web package")

    # Execute the command using subprocess
    result = subprocess.run(command, cwd=testdir.tmpdir, capture_output=True, text=True, env=env)

    assert result.returncode == 0, (
        f"Command failed with return code {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )

    assert "AssertionError" not in result.stderr, "AssertionError found in stderr"
