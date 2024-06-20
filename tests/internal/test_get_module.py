from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import mock_open
from unittest.mock import patch

import pytest
import toml

from ddtrace.internal.utils.get_inferred_service import _find_package_name
from ddtrace.internal.utils.get_inferred_service import _get_entrypoint_path_and_module
from ddtrace.internal.utils.get_inferred_service import _search_files


@pytest.fixture
def mock_psutil_process(mocker):
    def _mock_psutil_process(cmdline):
        mocker.patch("ddtrace.vendor.psutil.Process", return_value=MagicMock(cmdline=lambda: cmdline))

    return _mock_psutil_process


@pytest.fixture
def mock_search_files(mocker):
    def _mock_search_files(file_names, start_path):
        files = []
        for file_name in file_names:
            files.append(Path(f"/fake/path/{file_name}"))
        return files

    mocker.patch("ddtrace.internal.utils.get_inferred_service._search_files", side_effect=_mock_search_files)


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
        _, entrypoint_module = _get_entrypoint_path_and_module()
        assert entrypoint_module == expected_entrypoint


def test_find_package_name_setup_py(mock_search_files, mocker):
    setup_py_content = """
import hashlib
import os
import platform

if True:
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

    package_name = _find_package_name("/start/path")
    assert package_name == "ddtrace"


def test_find_package_name_pyproject_toml(mock_search_files, mocker):
    pyproject_toml_content = """

[project]
name = "some_project"
dynamic = ["version"]
description = "Datadog APM client library"
readme = "README.md"
"""

    mock_open_obj = mock_open(read_data=pyproject_toml_content)
    mocker.patch("builtins.open", mock_open_obj)
    mocker.patch("toml.load", return_value=toml.loads(pyproject_toml_content))

    package_name = _find_package_name("/start/path")
    assert package_name == "some_project"


def test_find_package_name_pyproject_poetry_toml(mock_search_files, mocker):
    pyproject_toml_content = """
[tool.poetry]
name = "your_project"
version = "0.1.0"
description = "A sample Python project"
authors = ["Your Name <you@example.com>"]

[tool.poetry.dependencies]
python == "^3.7"

[tool.poetry.dev-dependencies]
pytest == "^6.2"
pytest-mock == "^3.6"

"""

    mock_open_obj = mock_open(read_data=pyproject_toml_content)
    mocker.patch("builtins.open", mock_open_obj)
    mocker.patch("toml.load", return_value=toml.loads(pyproject_toml_content))

    package_name = _find_package_name("/start/path")
    assert package_name == "your_project"


def test_search_files(mocker):
    mocker.patch("pathlib.Path.exists", return_value=True)
    file_names = ["setup.py"]
    start_path = "/start/path"
    found_path = _search_files(file_names, start_path)
    assert found_path and found_path[0] == Path("/start/path/setup.py")

    file_names = ["pyproject.toml"]
    start_path = "/start/path"
    found_path = _search_files(file_names, start_path)
    assert found_path and found_path[0] == Path("/start/path/pyproject.toml")

    mocker.patch("pathlib.Path.exists", return_value=False)
    file_names = ["setup.py"]
    start_path = "/start/path"
    found_path = _search_files(file_names, start_path)
    assert not found_path
