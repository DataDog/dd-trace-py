import glob
import json
import os
import subprocess

import mock
import pytest
import six

from ddtrace.ext import ci
from ddtrace.ext import git


def _ci_fixtures():
    basepath = os.path.join(os.path.dirname(__file__), "fixtures", "ci")
    for filename in glob.glob(os.path.join(basepath, "*.json")):
        with open(filename) as fp:
            for i, item in enumerate(json.load(fp)):
                yield os.path.basename(filename)[:-5] + ":" + str(i), item[0], item[1]


def _updateenv(monkeypatch, env):
    for k, v in env.items():
        # monkeypatch logs a warning if values passed to setenv are not strings
        monkeypatch.setenv(str(k), str(v))


@pytest.fixture
def git_repo(tmpdir):
    """Create temporary git directory, with one added file commit with a unique author and committer."""
    cwd = str(tmpdir)
    subprocess.check_output("git init", cwd=cwd, shell=True)
    subprocess.check_output('git remote add origin "git@github.com:test-repo-url.git"', cwd=cwd, shell=True)
    # Set temporary git directory to not require gpg commit signing
    subprocess.check_output("git config --local commit.gpgsign false", cwd=cwd, shell=True)
    # Set committer user to be "Jane Doe"
    subprocess.check_output('git config --local user.name "Jane Doe"', cwd=cwd, shell=True)
    subprocess.check_output('git config --local user.email "jane@doe.com"', cwd=cwd, shell=True)
    subprocess.check_output("touch tmp.py", cwd=cwd, shell=True)
    subprocess.check_output("git add tmp.py", cwd=cwd, shell=True)
    # Override author to be "John Doe"
    subprocess.check_output(
        'GIT_COMMITTER_DATE="2021-01-20T04:37:21-0400" git commit --date="2021-01-19T09:24:53-0400" '
        '-m "this is a commit msg" --author="John Doe <john@doe.com>" --no-edit',
        cwd=cwd,
        shell=True,
    )
    yield cwd


@pytest.fixture
def git_repo_empty(tmpdir):
    """Create temporary empty git directory, meaning no commits/users/repository-url to extract (error)"""
    cwd = str(tmpdir)
    subprocess.check_output("git init", cwd=cwd, shell=True)
    yield cwd


@pytest.mark.parametrize("name,environment,tags", _ci_fixtures())
def test_ci_providers(monkeypatch, name, environment, tags):
    """Make sure all provided environment variables from each CI provider are tagged correctly."""
    _updateenv(monkeypatch, environment)
    extracted_tags = ci.tags(environment)
    for key, value in tags.items():
        assert extracted_tags[key] == value, "wrong tags in {0} for {1}".format(name, environment)


def test_git_extract_user_info(git_repo):
    """Make sure that git commit author/committer name, email, and date are extracted and tagged correctly."""
    expected_author = ("John Doe", "john@doe.com", "2021-01-19T09:24:53-0400")
    expected_committer = ("Jane Doe", "jane@doe.com", "2021-01-20T04:37:21-0400")
    extracted_users = git.extract_user_info(cwd=git_repo)

    assert extracted_users["author"] == expected_author
    assert extracted_users["committer"] == expected_committer


def test_git_extract_user_info_error(git_repo_empty):
    """On error, the author/committer tags should not be extracted, and should internally raise and log the error."""
    with pytest.raises(ValueError):
        git.extract_user_info(cwd=git_repo_empty)


def test_git_extract_repository_url(git_repo):
    """Make sure that the git repository url is extracted properly."""
    expected_repository_url = "git@github.com:test-repo-url.git"
    assert git.extract_repository_url(cwd=git_repo) == expected_repository_url


def test_git_extract_repository_url_error(git_repo_empty):
    """On error, the repository url tag should not be extracted, and should internally raise the error."""
    with pytest.raises(ValueError):
        git.extract_repository_url(cwd=git_repo_empty)


def test_git_extract_commit_message(git_repo):
    """Make sure that the git commit message is extracted properly."""
    expected_msg = "this is a commit msg"
    assert git.extract_commit_message(cwd=git_repo) == expected_msg


def test_git_extract_commit_message_error(git_repo_empty):
    """On error, the commit message tag should not be extracted, and should internally raise and log the error."""
    with pytest.raises(ValueError):
        git.extract_commit_message(cwd=git_repo_empty)


def test_extract_git_metadata(git_repo):
    """Test that extract_git_metadata() sets all tags correctly."""
    expected_tags = {
        git.REPOSITORY_URL: "git@github.com:test-repo-url.git",
        git.COMMIT_MESSAGE: "this is a commit msg",
        git.COMMIT_AUTHOR_NAME: "John Doe",
        git.COMMIT_AUTHOR_EMAIL: "john@doe.com",
        git.COMMIT_AUTHOR_DATE: "2021-01-19T09:24:53-0400",
        git.COMMIT_COMMITTER_NAME: "Jane Doe",
        git.COMMIT_COMMITTER_EMAIL: "jane@doe.com",
        git.COMMIT_COMMITTER_DATE: "2021-01-20T04:37:21-0400",
    }
    assert git.extract_git_metadata(cwd=git_repo) == expected_tags


def test_git_executable_not_found_error(git_repo_empty):
    """If git executable not available, should raise internally, log, and not extract any tags."""
    with mock.patch("ddtrace.ext.ci.git.log") as log:
        with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_popen:
            if six.PY2:
                mock_popen.side_effect = OSError()
            else:
                mock_popen.side_effect = FileNotFoundError()
            extracted_tags = git.extract_git_metadata(cwd=git_repo_empty)
        log.error.assert_called_with("Git executable not found, cannot extract git metadata.")

    assert extracted_tags == {}


def test_ci_provider_tags_not_overwritten_by_git_executable(git_repo):
    """If non-Falsey values from CI provider env, should not be overwritten by extracted git metadata."""
    ci_provider_env = {
        "APPVEYOR": "true",
        "APPVEYOR_BUILD_FOLDER": "/foo/bar",
        "APPVEYOR_BUILD_ID": "appveyor-build-id",
        "APPVEYOR_BUILD_NUMBER": "appveyor-pipeline-number",
        "APPVEYOR_REPO_BRANCH": "master",
        "APPVEYOR_REPO_COMMIT": "appveyor-repo-commit",
        "APPVEYOR_REPO_NAME": "appveyor-repo-name",
        "APPVEYOR_REPO_PROVIDER": "github",
        "APPVEYOR_REPO_COMMIT_MESSAGE_EXTENDED": "this is the correct commit message",
        "APPVEYOR_REPO_COMMIT_AUTHOR": "John Jack",
        "APPVEYOR_REPO_COMMIT_AUTHOR_EMAIL": "john@jack.com",
    }

    extracted_tags = ci.tags(env=ci_provider_env, cwd=git_repo)

    assert extracted_tags["git.repository_url"] == "https://github.com/appveyor-repo-name.git"
    assert extracted_tags["git.commit.message"] == "this is the correct commit message"
    assert extracted_tags["git.commit.author.name"] == "John Jack"
    assert extracted_tags["git.commit.author.email"] == "john@jack.com"


def test_falsey_ci_provider_values_overwritten_by_git_executable(git_repo):
    """If no or None or empty string values from CI provider env, should be overwritten by extracted git metadata."""
    ci_provider_env = {
        "APPVEYOR": "true",
        "APPVEYOR_BUILD_FOLDER": "/foo/bar",
        "APPVEYOR_BUILD_ID": "appveyor-build-id",
        "APPVEYOR_BUILD_NUMBER": "appveyor-pipeline-number",
        "APPVEYOR_REPO_BRANCH": "master",
        "APPVEYOR_REPO_COMMIT": "appveyor-repo-commit",
        "APPVEYOR_REPO_NAME": "appveyor-repo-name",
        "APPVEYOR_REPO_PROVIDER": "not-github",
        "APPVEYOR_REPO_COMMIT_MESSAGE_EXTENDED": None,
        "APPVEYOR_REPO_COMMIT_AUTHOR": "",
    }

    extracted_tags = ci.tags(env=ci_provider_env, cwd=git_repo)

    assert extracted_tags["git.repository_url"] == "git@github.com:test-repo-url.git"
    assert extracted_tags["git.commit.message"] == "this is a commit msg"
    assert extracted_tags["git.commit.author.name"] == "John Doe"
    assert extracted_tags["git.commit.author.email"] == "john@doe.com"


def test_os_runtime_metadata_tagging():
    """Ensure that OS and runtime metadata are added as tags."""
    os_runtime_tags = ci._get_runtime_and_os_metadata()
    assert os_runtime_tags.get(ci.OS_ARCHITECTURE) is not None
    assert os_runtime_tags.get(ci.OS_PLATFORM) is not None
    assert os_runtime_tags.get(ci.OS_VERSION) is not None
    assert os_runtime_tags.get(ci.RUNTIME_NAME) is not None
    assert os_runtime_tags.get(ci.RUNTIME_VERSION) is not None

    extracted_tags = ci.tags()
    assert extracted_tags.get(ci.OS_ARCHITECTURE) is not None
    assert extracted_tags.get(ci.OS_PLATFORM) is not None
    assert extracted_tags.get(ci.OS_VERSION) is not None
    assert extracted_tags.get(ci.RUNTIME_NAME) is not None
    assert extracted_tags.get(ci.RUNTIME_VERSION) is not None
