"""
tests for git metadata embedding and processing.
"""

import glob
import os
import subprocess

import pytest

from tests.utils import TracerTestCase


@pytest.fixture
def preapare_test_env(mypackage_example):
    subprocess.check_output("python setup.py bdist_wheel", shell=True)
    pkgfile = glob.glob(os.path.join(mypackage_example, "dist", "*.whl"))[0]

    os.environ["SHA_VALUE"] = subprocess.check_output("git rev-parse HEAD", shell=True).decode("utf-8").strip()

    envdir = os.path.join(mypackage_example, "run_env_dir")
    cwd = os.getcwd()
    python_path = os.getenv("PYTHONPATH", None)
    try:
        subprocess.check_output("pip install --target=" + envdir + " " + pkgfile, shell=True)
        os.chdir(envdir)
        os.environ["PYTHONPATH"] = os.getenv("PYTHONPATH", "") + os.pathsep + envdir
        yield
    finally:
        os.chdir(cwd)
        if python_path is not None:
            os.environ["PYTHONPATH"] = python_path


@pytest.mark.usefixtures("preapare_test_env")
class GitMetadataTestCase(TracerTestCase):
    def _scenario_test_gitmetadata_from_package(self):
        with self.tracer.trace("span") as s:
            pass

        assert s.get_tag("_dd.git.commit.sha") == os.getenv("SHA_VALUE")
        assert s.get_tag("_dd.git.repository_url") == "https://github.com/companydotcom/repo"
        assert s.get_tag("_dd.python_main_package") == "mypackage"

    def _scenario_test_gitmetadata_from_DD_TAGS(self):
        with self.tracer.trace("span") as s:
            pass

        # must be from DD_TAGS
        assert s.get_tag("_dd.git.commit.sha") == "12345"
        assert s.get_tag("_dd.git.repository_url") == "github.com/user/tag_repo"
        # must be not present in old tags
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None

    def _scenario_test_gitmetadata_from_ENV(self):
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

    def _scenario_test_gitmetadata_disabled(self):
        with self.tracer.trace("span") as s:
            pass

        # must not present
        assert s.get_tag("_dd.git.commit.sha") is None
        assert s.get_tag("_dd.git.repository_url") is None
        assert s.get_tag("_dd.python_main_package") is None
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None
        assert s.get_tag("dd.python_main_package") is None

    def _scenario_test_gitmetadata_package_without_metadata(self):
        with self.tracer.trace("span") as s:
            pass

        # must not present
        assert s.get_tag("_dd.git.commit.sha") is None
        assert s.get_tag("_dd.git.repository_url") is None
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None

    def _scenario_test_gitmetadata_from_env_filtering_https(self):
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

    def _scenario_test_gitmetadata_from_ddtags_filtering_https(self):
        with self.tracer.trace("span") as s:
            pass

        # must be from DD_TAGS
        assert s.get_tag("_dd.git.commit.sha") == "12345"
        assert s.get_tag("_dd.git.repository_url") == "https://github.com/user/tag_repo.git"
        # must be not present in old tags
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None

    def _scenario_test_gitmetadata_from_env_filtering_ssh(self):
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

    def _scenario_test_gitmetadata_from_ddtags_filtering_ssh(self):
        with self.tracer.trace("span") as s:
            pass

        # must be from DD_TAGS
        assert s.get_tag("_dd.git.commit.sha") == "12345"
        assert s.get_tag("_dd.git.repository_url") == "ssh://github.com/user/tag_repo.git"
        # must be not present in old tags
        assert s.get_tag("dd.git.repository_url") is None
        assert s.get_tag("dd.git.commit.sha") is None


@pytest.mark.subprocess(
    env={
        "DD_TAGS": "git.commit.sha:12345,git.repository_url:github.com/user/repo",
    }
)
def test_gitmetadata_caching(monkeypatch):
    from ddtrace.internal import gitmetadata

    repository_url, commit_sha, main_package = gitmetadata.get_git_tags()
    assert commit_sha == "12345"
    assert repository_url == "github.com/user/repo"

    # set new values
    gitmetadata.config.repository_url = "github.com/user/repo2"
    gitmetadata.config.commit_sha = "54321"
    gitmetadata.config.main_package = "mypackage"
    repository_url, commit_sha, main_package = gitmetadata.get_git_tags()
    # must have old values
    assert commit_sha == "12345"
    assert repository_url == "github.com/user/repo"


@pytest.mark.usefixtures("preapare_test_env")
@pytest.mark.subprocess(env=dict(DD_MAIN_PACKAGE="mypackage"), out=None, err=None, command=True, test_assertions=True)
def test_GitMetadataTestCase_gitmetadata_from_package():
    from tests.process import case_context
    from tests.tracer.test_gitmetadata import GitMetadataTestCase

    with case_context(GitMetadataTestCase, "_scenario_test_gitmetadata_from_package") as case:
        case._scenario_test_gitmetadata_from_package()


@pytest.mark.usefixtures("preapare_test_env")
@pytest.mark.subprocess(
    command=True,
    env=dict(DD_TAGS="git.commit.sha:12345,git.repository_url:github.com/user/tag_repo"),
    out=None,
    err=None,
    test_assertions=True,
)
def test_GitMetadataTestCase_gitmetadata_from_DD_TAGS():
    from tests.process import case_context
    from tests.tracer.test_gitmetadata import GitMetadataTestCase

    with case_context(GitMetadataTestCase, "_scenario_test_gitmetadata_from_DD_TAGS") as case:
        case._scenario_test_gitmetadata_from_DD_TAGS()


@pytest.mark.usefixtures("preapare_test_env")
@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="git.commit.sha:12345,git.repository_url:github.com/user/tag_repo",
        DD_GIT_COMMIT_SHA="123456",
        DD_GIT_REPOSITORY_URL="github.com/user/env_repo",
        DD_MAIN_PACKAGE="mypackage",
    ),
    out=None,
    err=None,
    command=True,
    test_assertions=True,
)
def test_GitMetadataTestCase_gitmetadata_from_ENV():
    from tests.process import case_context
    from tests.tracer.test_gitmetadata import GitMetadataTestCase

    with case_context(GitMetadataTestCase, "_scenario_test_gitmetadata_from_ENV") as case:
        case._scenario_test_gitmetadata_from_ENV()


@pytest.mark.usefixtures("preapare_test_env")
@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="git.commit.sha:12345,git.repository_url:github.com/user/tag_repo",
        DD_GIT_COMMIT_SHA="123456",
        DD_GIT_REPOSITORY_URL="github.com/user/env_repo",
        DD_MAIN_PACKAGE="mypackage",
        DD_TRACE_GIT_METADATA_ENABLED="false",
    ),
    out=None,
    err=None,
    command=True,
    test_assertions=True,
)
def test_GitMetadataTestCase_gitmetadata_disabled():
    from tests.process import case_context
    from tests.tracer.test_gitmetadata import GitMetadataTestCase

    with case_context(GitMetadataTestCase, "_scenario_test_gitmetadata_disabled") as case:
        case._scenario_test_gitmetadata_disabled()


@pytest.mark.usefixtures("preapare_test_env")
@pytest.mark.subprocess(env=dict(DD_MAIN_PACKAGE="pytest"), out=None, err=None, command=True, test_assertions=True)
def test_GitMetadataTestCase_gitmetadata_package_without_metadata():
    from tests.process import case_context
    from tests.tracer.test_gitmetadata import GitMetadataTestCase

    with case_context(GitMetadataTestCase, "_scenario_test_gitmetadata_package_without_metadata") as case:
        case._scenario_test_gitmetadata_package_without_metadata()


@pytest.mark.usefixtures("preapare_test_env")
@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="git.commit.sha:12345,git.repository_url:https://username:password@github.com/user/env_repo.git",
        DD_GIT_COMMIT_SHA="123456",
        DD_GIT_REPOSITORY_URL="https://username:password@github.com/user/env_repo.git",
        DD_MAIN_PACKAGE="mypackage",
    ),
    out=None,
    err=None,
    command=True,
    test_assertions=True,
)
def test_GitMetadataTestCase_gitmetadata_from_env_filtering_https():
    from tests.process import case_context
    from tests.tracer.test_gitmetadata import GitMetadataTestCase

    with case_context(GitMetadataTestCase, "_scenario_test_gitmetadata_from_env_filtering_https") as case:
        case._scenario_test_gitmetadata_from_env_filtering_https()


@pytest.mark.usefixtures("preapare_test_env")
@pytest.mark.subprocess(
    env=dict(DD_TAGS="git.commit.sha:12345,git.repository_url:https://username:password@github.com/user/tag_repo.git"),
    out=None,
    err=None,
    command=True,
    test_assertions=True,
)
def test_GitMetadataTestCase_gitmetadata_from_ddtags_filtering_https():
    from tests.process import case_context
    from tests.tracer.test_gitmetadata import GitMetadataTestCase

    with case_context(GitMetadataTestCase, "_scenario_test_gitmetadata_from_ddtags_filtering_https") as case:
        case._scenario_test_gitmetadata_from_ddtags_filtering_https()


@pytest.mark.usefixtures("preapare_test_env")
@pytest.mark.subprocess(
    env=dict(
        DD_TAGS="git.commit.sha:12345,git.repository_url:ssh://username@github.com/user/env_repo.git",
        DD_GIT_COMMIT_SHA="123456",
        DD_GIT_REPOSITORY_URL="ssh://username@github.com/user/env_repo.git",
        DD_MAIN_PACKAGE="mypackage",
    ),
    out=None,
    err=None,
    command=True,
    test_assertions=True,
)
def test_GitMetadataTestCase_gitmetadata_from_env_filtering_ssh():
    from tests.process import case_context
    from tests.tracer.test_gitmetadata import GitMetadataTestCase

    with case_context(GitMetadataTestCase, "_scenario_test_gitmetadata_from_env_filtering_ssh") as case:
        case._scenario_test_gitmetadata_from_env_filtering_ssh()


@pytest.mark.usefixtures("preapare_test_env")
@pytest.mark.subprocess(
    env=dict(DD_TAGS="git.commit.sha:12345,git.repository_url:ssh://username@github.com/user/tag_repo.git"),
    out=None,
    err=None,
    command=True,
    test_assertions=True,
)
def test_GitMetadataTestCase_gitmetadata_from_ddtags_filtering_ssh():
    from tests.process import case_context
    from tests.tracer.test_gitmetadata import GitMetadataTestCase

    with case_context(GitMetadataTestCase, "_scenario_test_gitmetadata_from_ddtags_filtering_ssh") as case:
        case._scenario_test_gitmetadata_from_ddtags_filtering_ssh()
