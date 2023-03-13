# -*- coding: utf-8 -*-
"""
tests for git metadata embedding and processing.
"""
import os
import glob
import pytest
import ddtrace
import tempfile
import subprocess

from tests.subprocesstest import run_in_subprocess
from tests.utils import TracerTestCase


class GitMetadataTestCase(TracerTestCase):
    def _run(self, args):
        try:
            p = subprocess.Popen(args, stdout=subprocess.PIPE)
        except EnvironmentError:
            print("Couldn't run: ", args)
            return
        out = p.communicate()[0]
        return out.strip().decode("utf-8")

    def _copy_ddgitmetadata(self, tmpdirname):
        cdir = os.path.split(os.path.realpath(__file__))[0]
        ddgdir = os.path.join(cdir, "../../ddtrace/ddgitmetadata")
        self._run(["cp", "-r", ddgdir, tmpdirname + "/"])

    def _create_package_git_repo(self, tmpdirname):
        self._run(["git", "init", "-q"])
        with open(os.path.join(tmpdirname, "setup.py"), "w") as f:
            f.write(
                """
import ddgitmetadata

from setuptools import setup

setup(
    name="gitmetadatapoc",
    version="1.0",
    description="Python Test",
    author="First Last",
    author_email="example@mail.net",
    packages=["git_metadata_poc"],
    zip_safe=True,
)"""
            )
        self._run(["git", "add", "setup.py"])

        mdir = os.path.join(tmpdirname, "git_metadata_poc")
        os.mkdir(mdir)
        with open(os.path.join(mdir, "__init__.py"), "w") as f:
            f.write("")
        self._run(["git", "add", "git_metadata_poc"])
        self._run(["git", "commit", "-m", "'init'"])
        self._run(["git", "remote", "add", "origin", "git@github.com:user/repo.git"])

    def _build_package(self, tmpdirname):
        self._copy_ddgitmetadata(tmpdirname)
        self._run(["python3", "setup.py", "bdist_wheel"])

    def _install_package(self, tmpdirname):
        pkgfile = glob.glob(os.path.join(tmpdirname, "dist", "*.whl"))[0]
        envdir = os.path.join(tmpdirname, "env")
        self._run(["pip", "install", "--target=" + envdir, pkgfile])
        os.environ["PYTHONPATH"] = os.getenv("PYTHONPATH", "") + os.pathsep + envdir
        os.environ["SHA_VALUE"] = self._run(["git", "rev-parse", "HEAD"])

        # TODO: remove
        self._run(["cp", "-r", tmpdirname, "/tmp/out/"])

    def _preapare_test_env(self, tmpdirname):
        cwd = os.getcwd()
        python_path = os.getenv("PYTHONPATH", None)
        try:
            os.chdir(tmpdirname)
            self._create_package_git_repo(tmpdirname)
            self._build_package(tmpdirname)
            self._install_package(tmpdirname)
        finally:
            os.chdir(cwd)
            if python_path is not None:
                os.environ["PYTHONPATH"] = python_path

    @pytest.fixture(autouse=True)
    def setup(self, tmpdir):
        with tempfile.TemporaryDirectory() as tmpdirname:
            self._preapare_test_env(tmpdirname)
            yield

    @run_in_subprocess(
        env_overrides=dict(
            DD_TAGS="service:s,env:e,version:v",
            DD_ENV="env",
            DD_SERVICE="svc",
            DD_VERSION="0.123",
        )
    )
    def test_tags_from_DD_TAGS(self):
        t = ddtrace.Tracer()
        with t.trace("test") as s:
            assert s.service == "svc"
            assert s.get_tag("env") == "env"
            assert s.get_tag("version") == "0.123"
