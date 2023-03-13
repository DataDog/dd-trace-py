# -*- coding: utf-8 -*-
"""
tests for git metadata embedding and processing.
"""
import glob
import mock
import pytest
import os
import subprocess
import tempfile

import ddtrace
from ddtrace.internal.writer import AgentWriter
from tests.subprocesstest import run_in_subprocess
from tests.utils import TracerTestCase


def _run(args):
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE)
    except EnvironmentError:
        print("Couldn't run: ", args)
        return
    out = p.communicate()[0]
    return out.strip().decode("utf-8")


def _copy_ddgitmetadata(tmpdirname):
    cdir = os.path.split(os.path.realpath(__file__))[0]
    ddgdir = os.path.join(cdir, "../../ddtrace/ddgitmetadata")
    _run(["cp", "-r", ddgdir, tmpdirname + "/"])


def _create_package_git_repo(tmpdirname):
    _run(["git", "init", "-q"])
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
    _run(["git", "add", "setup.py"])

    mdir = os.path.join(tmpdirname, "git_metadata_poc")
    os.mkdir(mdir)
    with open(os.path.join(mdir, "__init__.py"), "w") as f:
        f.write("")
    _run(["git", "add", "git_metadata_poc"])
    _run(["git", "commit", "-m", "'init'"])
    _run(["git", "remote", "add", "origin", "git@github.com:user/repo.git"])


def _build_package(tmpdirname):
    _copy_ddgitmetadata(tmpdirname)
    _run(["python3", "setup.py", "bdist_wheel"])


def _install_package(tmpdirname):
    pkgfile = glob.glob(os.path.join(tmpdirname, "dist", "*.whl"))[0]
    envdir = os.path.join(tmpdirname, "env")
    _run(["pip", "install", "--target=" + envdir, pkgfile])
    os.environ["PYTHONPATH"] = os.getenv("PYTHONPATH", "") + os.pathsep + envdir
    os.environ["SHA_VALUE"] = _run(["git", "rev-parse", "HEAD"])


def _preapare_test_env(tmpdirname):
    cwd = os.getcwd()
    python_path = os.getenv("PYTHONPATH", None)
    try:
        os.chdir(tmpdirname)
        _create_package_git_repo(tmpdirname)
        _build_package(tmpdirname)
        _install_package(tmpdirname)
    finally:
        os.chdir(cwd)
        if python_path is not None:
            os.environ["PYTHONPATH"] = python_path


_tmpdir = None


class GitMetadataTestCase(TracerTestCase):
    def setup_class(cls):
        global _tmpdir
        _tmpdir = tempfile.TemporaryDirectory()
        _preapare_test_env(_tmpdir.name)

    def teardown_class(cls):
        global _tmpdir
        _tmpdir.cleanup()

    @run_in_subprocess(
        env_overrides=dict(
            DD_MAIN_PACKAGE="gitmetadatapoc",
        )
    )
    def test_gitmetadata_from_package(self):
        writer = AgentWriter("http://foo:1234")
        t = ddtrace.Tracer()
        t.configure(writer=writer)
        with t.trace("test") as s:
            writer.write([s])

            assert s.get_tag("_dd.git.commit.sha") == os.getenv("SHA_VALUE")
            assert s.get_tag("_dd.git.repository_url") == "https://github.com/user/repo"

    @run_in_subprocess(
        env_overrides=dict(
            DD_TAGS="git.commit.sha:12345,git.repository_url:github.com/user/tag_repo",
            DD_MAIN_PACKAGE_="gitmetadatapoc",
        )
    )
    def test_gitmetadata_from_DD_TAGS(self):
        writer = AgentWriter("http://foo:1234")
        t = ddtrace.Tracer()
        t.configure(writer=writer)
        with t.trace("test") as s:
            writer.write([s])

            # must be from DD_TAGS
            assert s.get_tag("_dd.git.commit.sha") == "12345"
            assert s.get_tag("_dd.git.repository_url") == "github.com/user/tag_repo"
            # must be not present in old tags
            assert s.get_tag("dd.git.repository_url") == None
            assert s.get_tag("dd.git.commit.sha") == None

    @run_in_subprocess(
        env_overrides=dict(
            DD_TAGS="git.commit.sha:12345,git.repository_url:github.com/user/tag_repo",
            DD_GIT_COMMIT_SHA="123456",
            DD_GIT_REPOSITORY_URL="github.com/user/env_repo",
            DD_MAIN_PACKAGE="gitmetadatapoc",
        )
    )
    def test_gitmetadata_from_ENV(self):
        writer = AgentWriter("http://foo:1234")
        t = ddtrace.Tracer()
        t.configure(writer=writer)
        with t.trace("test") as s:
            writer.write([s])

            # must be from env variables
            assert s.get_tag("_dd.git.commit.sha") == "123456"
            assert s.get_tag("_dd.git.repository_url") == "github.com/user/env_repo"
            # must be not present in old tags
            assert s.get_tag("dd.git.repository_url") == None
            assert s.get_tag("dd.git.commit.sha") == None

    @run_in_subprocess(
        env_overrides=dict(
            DD_TAGS="git.commit.sha:12345,git.repository_url:github.com/user/tag_repo",
            DD_GIT_COMMIT_SHA="123456",
            DD_GIT_REPOSITORY_URL="github.com/user/env_repo",
            DD_MAIN_PACKAGE="gitmetadatapoc",
            DD_TRACE_GIT_METADATA_ENABLED="false",
        )
    )
    def test_gitmetadata_disabled(self):
        writer = AgentWriter("http://foo:1234")
        t = ddtrace.Tracer()
        t.configure(writer=writer)
        with t.trace("test") as s:
            writer.write([s])

            # must not present
            assert s.get_tag("_dd.git.commit.sha") == None
            assert s.get_tag("_dd.git.repository_url") == None
            assert s.get_tag("dd.git.repository_url") == None
            assert s.get_tag("dd.git.commit.sha") == None
