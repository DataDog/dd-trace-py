from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import mock_open
from unittest.mock import patch

import pytest
import toml

from ddtrace.internal.utils.get_module import find_package_name
from ddtrace.internal.utils.get_module import get_entrypoint_path_and_module
from ddtrace.internal.utils.get_module import search_files


@pytest.fixture
def mock_psutil_process(mocker):
    def _mock_psutil_process(cmdline):
        mocker.patch("psutil.Process", return_value=MagicMock(cmdline=lambda: cmdline))

    return _mock_psutil_process


@pytest.mark.parametrize(
    "cmdline, expected_entrypoint",
    [
        (["python", "-m", "tests.contrib"], "tests.contrib"),
        (["python", "-m", "tests"], "tests"),
        (["python", "some_dir/script.py"], "some_dir.script"),
        (["python", "script.py"], "script"),
        (["python", "non-existent-file.py"], None),
        (["python", "-c", 'print("Hello World")'], None),
        (["/usr/bin/python3", "-m", "ddtrace.contrib"], "ddtrace.contrib"),
        (["/usr/bin/python3", "script.py"], "script"),
        (["ddtrace-run", "python", "-m", "tests.contrib.django"], "tests.contrib.django"),
        (["ddtrace-run", "python3", "script.py"], "script"),
        (["ddtrace-run", "flask", "run"], None),
        (["flask", "run"], None),
        (["django-admin", "runserver"], None),
        (["gunicorn", "test.contrib"], None),
        (["uvicorn", "myapp.asgi:app"], None),
    ],
)
def test_get_entrypoint_path(mock_psutil_process, cmdline, expected_entrypoint):
    mock_psutil_process(cmdline)

    with patch("pathlib.Path.exists") as mock_exists:
        # Make exists() return True for all paths ending with 'script.py'
        mock_exists.side_effect = lambda: any(arg.endswith("script.py") for arg in cmdline)
        _, entrypoint_module = get_entrypoint_path_and_module()
        assert entrypoint_module == expected_entrypoint


@pytest.fixture
def mock_search_files(mocker):
    def _mock_search_files(file_names, start_path):
        if "setup.py" in file_names:
            return Path("/fake/path/setup.py")
        elif "pyproject.toml" in file_names:
            return Path("/fake/path/pyproject.toml")
        return None

    mocker.patch("ddtrace.internal.utils.get_module.search_files", side_effect=_mock_search_files)


def test_find_package_name_setup_py(mock_search_files, mocker):
    setup_py_content = """
    import hashlib
    import os
    import platform

    if:
        something()
    else:
        something_else()

    setup(
        name="ddtrace",
        packages=find_packages(exclude=["tests*", "benchmarks*", "scripts*"]),
        package_data={
            "ddtrace": ["py.typed"],
            "ddtrace.appsec": ["rules.json"],
            "ddtrace.appsec._ddwaf": ["libddwaf/*/lib/libddwaf.*"],
            "ddtrace.appsec._iast._taint_tracking": ["CMakeLists.txt"],
            "ddtrace.internal.datadog.profiling": ["libdd_wrapper.*"],
        },
    )
    """
    mock_open_obj = mock_open(read_data=setup_py_content)
    mocker.patch("builtins.open", mock_open_obj)

    package_name = find_package_name("/start/path")
    assert package_name == "ddtrace"


def test_find_package_name_pyproject_toml(mock_search_files, mocker):
    pyproject_toml_content = """
    [build-system]
    requires = ["setuptools_scm[toml]>=4", "cython", "cmake>=3.24.2,<3.28; python_version>='3.7'"]
    build-backend = "setuptools.build_meta"

    [project]
    name = "some_project"
    dynamic = ["version"]
    description = "Datadog APM client library"
    readme = "README.md"
    license = { text = "LICENSE.BSD3" }
    requires-python = ">=3.7"
    authors = [
        { name = "Datadog, Inc.", email = "dev@datadoghq.com" },
    ]
    """

    mock_open_obj = mock_open(read_data=pyproject_toml_content)
    mocker.patch("builtins.open", mock_open_obj)
    mocker.patch("toml.load", return_value=toml.loads(pyproject_toml_content))

    package_name = find_package_name("/start/path")
    assert package_name == "some_project"


def test_find_package_name_pyproject_poetry_toml(mock_search_files, mocker):
    pyproject_toml_content = """
    [tool.poetry]
    name = "your_project"
    version = "0.1.0"
    description = "A sample Python project"
    authors = ["Your Name <you@example.com>"]
    packages = [
        { include = "your_module", from = "src" }
    ]

    [tool.poetry.dependencies]
    python = "^3.7"

    [tool.poetry.dev-dependencies]
    pytest = "^6.2"
    pytest-mock = "^3.6"

    [build-system]
    requires = ["poetry-core>=1.0.0"]
    build-backend = "poetry.core.masonry.api"
    """

    mock_open_obj = mock_open(read_data=pyproject_toml_content)
    mocker.patch("builtins.open", mock_open_obj)
    mocker.patch("toml.load", return_value=toml.loads(pyproject_toml_content))

    package_name = find_package_name("/start/path")
    assert package_name == "your_project"


def test_search_files(mocker):
    mocker.patch("pathlib.Path.exists", return_value=True)
    file_names = ["setup.py"]
    start_path = "/start/path"
    found_path = search_files(file_names, start_path)
    assert found_path == Path("/start/path/setup.py")

    file_names = ["pyproject.toml"]
    start_path = "/start/path"
    found_path = search_files(file_names, start_path)
    assert found_path == Path("/start/path/pyproject.toml")

    mocker.patch("pathlib.Path.exists", return_value=False)
    file_names = ["setup.py"]
    start_path = "/start/path"
    found_path = search_files(file_names, start_path)
    assert found_path is None
