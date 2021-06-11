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
    subprocess.check_output("git init", cwd=tmpdir, shell=True)
    subprocess.check_output('git remote add origin "git@github.com:test-repo-url.git"', cwd=tmpdir, shell=True)
    subprocess.check_output('git config user.name "John Doe"', cwd=tmpdir, shell=True)
    subprocess.check_output('git config user.email "john@doe.com"', cwd=tmpdir, shell=True)
    subprocess.check_output("touch tmp.py", cwd=tmpdir, shell=True)
    subprocess.check_output("git add tmp.py", cwd=tmpdir, shell=True)
    subprocess.check_output(
        'git commit --date="2021-01-19T09:24:53-0400" -m "this is a commit msg" --no-edit', cwd=tmpdir, shell=True
    )
    subprocess.check_output(
        'GIT_COMMITTER_DATE="2021-01-20T04:37:21-0400" git -c user.name="Jane Doe" -c user.email="jane@doe.com" commit '
        "--amend --no-edit",
        cwd=tmpdir,
        shell=True,
    )
    yield str(tmpdir)


@pytest.fixture
def git_repo_error(tmpdir):
    subprocess.check_output("git init", cwd=tmpdir, shell=True)
    yield tmpdir


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


def test_git_extract_user_info_error(git_repo_error):
    """On error, the author/committer tags should not be extracted, and should internally raise and log the error."""
    with pytest.raises(ValueError):
        git.extract_user_info(cwd=git_repo_error)


def test_git_extract_repository_url(git_repo):
    """Make sure that the git repository url is extracted properly."""
    expected_repository_url = "git@github.com:test-repo-url.git"
    assert git.extract_repository_url(cwd=git_repo) == expected_repository_url


def test_git_extract_repository_url_error(git_repo_error):
    """On error, the repository url tag should not be extracted, and should internally raise the error."""
    with pytest.raises(ValueError):
        git.extract_repository_url(cwd=git_repo_error)


def test_git_extract_commit_message(git_repo):
    """Make sure that the git commit message is extracted properly."""
    expected_msg = "this is a commit msg"
    assert git.extract_commit_message(cwd=git_repo) == expected_msg


def test_git_extract_commit_message_error(git_repo_error):
    """On error, the commit message tag should not be extracted, and should internally raise and log the error."""
    with pytest.raises(ValueError):
        git.extract_commit_message(cwd=git_repo_error)


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


def test_git_executable_not_found_error(git_repo_error):
    """If git executable not available, should raise internally, log, and not extract any tags."""
    with mock.patch("ddtrace.ext.ci.git.log") as log:
        with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_popen:
            if six.PY2:
                mock_popen.side_effect = OSError()
            else:
                mock_popen.side_effect = FileNotFoundError()
            extracted_tags = git.extract_git_metadata(cwd=git_repo_error)
        log.error.assert_called_with("Git executable not found, cannot extract git metadata.")

    assert extracted_tags == {}
