"""
tests for git metadata embedding and processing.
"""

import os
import subprocess
import sys

import pytest

from tests.subprocesstest import run_in_subprocess
from tests.utils import DDTRACE_PATH
from tests.utils import TracerTestCase


@pytest.fixture(scope="module")
def installed_package(tmp_path_factory):
    package_dir = tmp_path_factory.mktemp("gitmetadata-package")
    subprocess.check_output(
        ["git", "clone", "--shared", "--no-checkout", "--single-branch", str(DDTRACE_PATH), str(package_dir)]
    )
    subprocess.check_output(
        ["git", "remote", "set-url", "origin", "https://username:password@github.com/companydotcom/repo.git"],
        cwd=package_dir,
    )
    (package_dir / "mypackage").mkdir()
    (package_dir / "mypackage" / "__init__.py").write_text('"0.0.1"')
    (package_dir / "setup.py").write_text(
        "import ddtrace.sourcecode.setuptools_auto\n"
        "from setuptools import setup\n"
        "setup(name='mypackage', version='0.0.1')\n"
    )

    build_env = os.environ.copy()
    build_env.pop("DD_GIT_COMMIT_SHA", None)
    build_env.pop("DD_GIT_REPOSITORY_URL", None)
    subprocess.check_output([sys.executable, "setup.py", "bdist_wheel"], cwd=package_dir, env=build_env)
    pkgfile = next((package_dir / "dist").glob("*.whl"))
    envdir = package_dir / "run_env_dir"
    subprocess.check_output(["uv", "pip", "install", "--target", str(envdir), str(pkgfile)], cwd=DDTRACE_PATH)
    commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=package_dir, text=True).strip()
    return envdir, commit_sha


@pytest.fixture
def prepare_test_env(installed_package, monkeypatch, remove_git_repo_url_from_test_env):
    envdir, commit_sha = installed_package
    monkeypatch.setenv("SHA_VALUE", commit_sha)
    monkeypatch.setenv("PYTHONPATH", os.getenv("PYTHONPATH", "") + os.pathsep + str(envdir))
    monkeypatch.chdir(envdir)


@pytest.mark.usefixtures("prepare_test_env")
class GitMetadataTestCase(TracerTestCase):
    @run_in_subprocess(
        env_overrides=dict(
            DD_MAIN_PACKAGE="mypackage",
        )
    )
    def test_gitmetadata_from_package(self):
        with self.tracer.trace("span") as s:
            pass

        assert s.get_tag("_dd.git.commit.sha") == os.getenv("SHA_VALUE")
        assert s.get_tag("_dd.git.repository_url") == "https://github.com/companydotcom/repo"
        assert s.get_tag("_dd.python_main_package") == "mypackage"

    @run_in_subprocess(
        env_overrides=dict(
            DD_TAGS="git.commit.sha:12345,git.repository_url:github.com/user/tag_repo",
        )
    )
    def test_gitmetadata_from_DD_TAGS(self):
        with self.tracer.trace("span") as s:
            pass

        # must be from DD_TAGS
        assert s.get_tag("_dd.git.commit.sha") == "12345"
        assert s.get_tag("_dd.git.repository_url") == "github.com/user/tag_repo"
        # must be not present in old tags
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None

    @run_in_subprocess(
        env_overrides=dict(
            DD_TAGS="git.commit.sha:12345,git.repository_url:github.com/user/tag_repo",
            DD_GIT_COMMIT_SHA="123456",
            DD_GIT_REPOSITORY_URL="github.com/user/env_repo",
            DD_MAIN_PACKAGE="mypackage",
        )
    )
    def test_gitmetadata_from_ENV(self):
        with self.tracer.trace("span") as s:
            pass

        # must be from env variables
        assert s.get_tag("_dd.git.commit.sha") == "123456"
        assert s.get_tag("_dd.git.repository_url") == "github.com/user/env_repo"
        assert s.get_tag("_dd.python_main_package") == "mypackage"
        # must be not present in old tags
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None
        assert s.get_tag("dd.python_main_package") is None

    @run_in_subprocess(
        env_overrides=dict(
            DD_TAGS="git.commit.sha:12345,git.repository_url:github.com/user/tag_repo",
            DD_GIT_COMMIT_SHA="123456",
            DD_GIT_REPOSITORY_URL="github.com/user/env_repo",
            DD_MAIN_PACKAGE="mypackage",
            DD_TRACE_GIT_METADATA_ENABLED="false",
        )
    )
    def test_gitmetadata_disabled(self):
        with self.tracer.trace("span") as s:
            pass

        # must not present
        assert s.get_tag("_dd.git.commit.sha") is None
        assert s.get_tag("_dd.git.repository_url") is None
        assert s.get_tag("_dd.python_main_package") is None
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None
        assert s.get_tag("dd.python_main_package") is None

    @run_in_subprocess(
        env_overrides=dict(
            DD_MAIN_PACKAGE="pytest",
        )
    )
    def test_gitmetadata_package_without_metadata(self):
        with self.tracer.trace("span") as s:
            pass

        # must not present
        assert s.get_tag("_dd.git.commit.sha") is None
        assert s.get_tag("_dd.git.repository_url") is None
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None

    @run_in_subprocess(
        env_overrides=dict(
            DD_TAGS="git.commit.sha:12345,git.repository_url:https://username:password@github.com/user/env_repo.git",
            DD_GIT_COMMIT_SHA="123456",
            DD_GIT_REPOSITORY_URL="https://username:password@github.com/user/env_repo.git",
            DD_MAIN_PACKAGE="mypackage",
        )
    )
    def test_gitmetadata_from_env_filtering_https(self):
        with self.tracer.trace("span") as s:
            pass

        # must be from env variables
        assert s.get_tag("_dd.git.commit.sha") == "123456"
        assert s.get_tag("_dd.git.repository_url") == "https://github.com/user/env_repo.git"
        assert s.get_tag("_dd.python_main_package") == "mypackage"
        # must be not present in old tags
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None
        assert s.get_tag("dd.python_main_package") is None

    @run_in_subprocess(
        env_overrides=dict(
            DD_TAGS="git.commit.sha:12345,git.repository_url:https://username:password@github.com/user/tag_repo.git",
        )
    )
    def test_gitmetadata_from_ddtags_filtering_https(self):
        with self.tracer.trace("span") as s:
            pass

        # must be from DD_TAGS
        assert s.get_tag("_dd.git.commit.sha") == "12345"
        assert s.get_tag("_dd.git.repository_url") == "https://github.com/user/tag_repo.git"
        # must be not present in old tags
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None

    @run_in_subprocess(
        env_overrides=dict(
            DD_TAGS="git.commit.sha:12345,git.repository_url:ssh://username@github.com/user/env_repo.git",
            DD_GIT_COMMIT_SHA="123456",
            DD_GIT_REPOSITORY_URL="ssh://username@github.com/user/env_repo.git",
            DD_MAIN_PACKAGE="mypackage",
        )
    )
    def test_gitmetadata_from_env_filtering_ssh(self):
        with self.tracer.trace("span") as s:
            pass

        # must be from env variables
        assert s.get_tag("_dd.git.commit.sha") == "123456"
        assert s.get_tag("_dd.git.repository_url") == "ssh://github.com/user/env_repo.git"
        assert s.get_tag("_dd.python_main_package") == "mypackage"
        # must be not present in old tags
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None
        assert s.get_tag("dd.python_main_package") is None

    @run_in_subprocess(
        env_overrides=dict(
            DD_TAGS="git.commit.sha:12345,git.repository_url:ssh://username@github.com/user/tag_repo.git",
        )
    )
    def test_gitmetadata_from_ddtags_filtering_ssh(self):
        with self.tracer.trace("span") as s:
            pass

        # must be from DD_TAGS
        assert s.get_tag("_dd.git.commit.sha") == "12345"
        assert s.get_tag("_dd.git.repository_url") == "ssh://github.com/user/tag_repo.git"
        # must be not present in old tags
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None
