import contextlib
import os
import shutil
import subprocess
import tempfile

import pytest


def _run(cmd):
    return subprocess.check_output(cmd, shell=True)


@contextlib.contextmanager
def create_package(directory, pyproject, setup):
    package_dir = os.path.join(directory, "mypackage")
    os.mkdir(package_dir)

    pyproject_file = os.path.join(package_dir, "pyproject.toml")
    with open(pyproject_file, "wb") as f:
        f.write(pyproject.encode("utf-8"))

    setup_file = os.path.join(package_dir, "setup.py")
    with open(setup_file, "wb") as f:
        f.write(setup.encode("utf-8"))

    _ = os.path.join(package_dir, "mypackage")
    os.mkdir(_)
    with open(os.path.join(_, "__init__.py"), "wb") as f:
        f.write('"0.0.1"'.encode("utf-8"))

    cwd = os.getcwd()
    os.chdir(package_dir)

    try:
        _run("git init")
        _run("git config --local user.name user")
        _run("git config --local user.email user@company.com")
        _run("git add .")
        _run("git commit -m init")
        _run("git remote add origin https://github.com/companydotcom/repo.git")

        yield package_dir
    finally:
        os.chdir(cwd)

        try:
            shutil.rmtree(package_dir)
        except OSError:
            pass


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp()
    try:
        d = os.path.realpath(d)
        yield d
    finally:
        try:
            shutil.rmtree(d)
        except OSError:
            pass


@pytest.fixture
def example(temp_dir):
    with create_package(
        temp_dir,
        """\
[build-system]
requires = ["setuptools", "ddtrace"]
build-backend = "setuptools.build_meta"
""",
        """\
import ddtrace.sourcecode.setuptools_auto
from setuptools import setup

setup(
    name="mypackage",
    version="0.0.1",
)
""",
    ) as package:
        yield package
