import glob
import json
import os

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


@pytest.mark.parametrize("name,environment,tags", _ci_fixtures())
def test_ci_providers(monkeypatch, name, environment, tags):
    """Make sure all provided environment variables from each CI provider are tagged correctly."""
    _updateenv(monkeypatch, environment)
    extracted_tags = ci.tags(environment)
    for key, value in tags.items():
        assert extracted_tags[key] == value, "wrong tags in {0} for {1}".format(name, environment)


def test_git_extract_user_info_author():
    """Make sure that git commit author name, email, and date are extracted and tagged correctly."""
    expected_author = ("John Doe", "john@doe.com", "2021-02-19T08:24:53Z")
    expected_call_format = ["git", "show", "-s", "--format=%an,%ae,%ad", "--date=format:%Y-%m-%dT%H:%M:%S%z"]
    mock_author_output = b"John Doe,john@doe.com,2021-02-19T08:24:53Z"

    with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_popen:
        mock_popen.return_value.returncode = 0
        mock_popen.return_value.communicate.return_value = (mock_author_output, b"")
        extracted_author = git.extract_user_info(author=True)
        assert mock_popen.call_args[0][0] == expected_call_format
        extracted_tags = ci.tags()

    assert extracted_author == expected_author
    assert extracted_tags["git.commit.author.name"] == expected_author[0]
    assert extracted_tags["git.commit.author.email"] == expected_author[1]
    assert extracted_tags["git.commit.author.date"] == expected_author[2]


def test_git_extract_user_info_committer():
    """Make sure that git commit committer name, email, and date are extracted and tagged correctly."""
    expected_committer = ("Jane Doe", "jane@doe.com", "2021-01-19T09:24:53Z")
    expected_call_format = ["git", "show", "-s", "--format=%cn,%ce,%cd", "--date=format:%Y-%m-%dT%H:%M:%S%z"]
    mock_committer_output = b"Jane Doe,jane@doe.com,2021-01-19T09:24:53Z"

    with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_popen:
        mock_popen.return_value.returncode = 0
        mock_popen.return_value.communicate.return_value = (mock_committer_output, b"")
        extracted_committer = git.extract_user_info(author=False)
        assert mock_popen.call_args[0][0] == expected_call_format
        extracted_tags = ci.tags()

    assert extracted_committer == expected_committer
    assert extracted_tags["git.commit.committer.name"] == expected_committer[0]
    assert extracted_tags["git.commit.committer.email"] == expected_committer[1]
    assert extracted_tags["git.commit.committer.date"] == expected_committer[2]


def test_git_extract_user_info_error():
    """On error, the author/committer tags should not be extracted, and should internally raise and log the error."""
    with pytest.raises(ValueError):
        with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_popen:
            mock_popen.return_value.returncode = -1
            mock_popen.return_value.communicate.return_value = (b"", b"")
            git.extract_user_info(author=True)


def test_git_extract_repository_url():
    """Make sure that the git repository url is extracted properly."""
    expected_repository_url = "https://github.com/scope-demo/scopeagent-reference-springboot2.git"
    mock_repository_url_output = b"https://github.com/scope-demo/scopeagent-reference-springboot2.git"
    with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_popen:
        mock_popen.return_value.returncode = 0
        mock_popen.return_value.communicate.return_value = (mock_repository_url_output, b"")
        extracted_repository_url = git.extract_repository_url()

    assert extracted_repository_url == expected_repository_url


def test_git_extract_repository_url_error():
    """On error, the repository url tag should not be extracted, and should internally raise and log the error."""
    with pytest.raises(ValueError):
        with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_popen:
            mock_popen.return_value.returncode = -1
            mock_popen.return_value.communicate.return_value = (b"", b"")
            git.extract_repository_url()


def test_git_extract_commit_message():
    """Make sure that the git commit message is extracted properly."""
    expected_msg = "Update README.md"
    mock_output = b"Update README.md"
    with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_popen:
        mock_popen.return_value.returncode = 0
        mock_popen.return_value.communicate.return_value = (mock_output, b"")
        extracted_msg = git.extract_commit_message()

    assert extracted_msg == expected_msg


def test_git_extract_commit_message_error():
    """On error, the commit message tag should not be extracted, and should internally raise and log the error."""
    with pytest.raises(ValueError):
        with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_popen:
            mock_popen.return_value.returncode = -1
            mock_popen.return_value.communicate.return_value = (b"", b"")
            git.extract_commit_message()


def test_git_executable_not_found_error():
    """If git executable not available, should raise internally, log, and not extract any tags."""
    with mock.patch("ddtrace.ext.ci.git.log") as log:
        with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_popen:
            if six.PY2:
                mock_popen.side_effect = OSError()
            else:
                mock_popen.side_effect = FileNotFoundError()
            extracted_tags = git.extract_git_metadata()
        log.error.assert_called_with("Git executable not found, cannot extract git metadata.")

    assert "git.repository_url" not in extracted_tags
    assert "git.commit.message" not in extracted_tags
    assert "git.commit.author.name" not in extracted_tags
    assert "git.commit.author.email" not in extracted_tags
    assert "git.commit.author.date" not in extracted_tags
    assert "git.commit.committer.name" not in extracted_tags
    assert "git.commit.committer.email" not in extracted_tags
    assert "git.commit.committer.date" not in extracted_tags


def test_ci_provider_tags_not_overwritten_by_git_executable():
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
        "APPVEYOR_REPO_COMMIT_MESSAGE": "this is the correct commit message",
        "APPVEYOR_REPO_COMMIT_AUTHOR": "John Doe",
        "APPVEYOR_REPO_COMMIT_AUTHOR_EMAIL": "jdoe@gmail.com",
    }

    with mock.patch("ddtrace.ext.ci.git.extract_git_metadata") as mock_git_data:
        mock_git_data.return_value = {
            "git.repository_url": "clearly-wrong-repository-url",
            "git.commit.message": "clearly-wrong-commit-message",
            "git.commit.author.name": "clearly-wrong-commit-author-name",
            "git.commit.author.email": "clearly-wrong-commit-author-email",
            "git.commit.author.date": "2021-01-19T09:24:53Z",
            "git.commit.committer.name": "Jane Doe",
            "git.commit.committer.email": "jane_doe@gmail.com",
            "git.commit.committer.date": "2021-01-19T09:24:53Z",
        }
        extracted_tags = ci.tags(ci_provider_env)

    assert extracted_tags["git.repository_url"] == "https://github.com/appveyor-repo-name.git"
    assert extracted_tags["git.commit.message"] == "this is the correct commit message"
    assert extracted_tags["git.commit.author.name"] == "John Doe"
    assert extracted_tags["git.commit.author.email"] == "jdoe@gmail.com"


def test_falsey_ci_provider_values_overwritten_by_git_executable():
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
        "APPVEYOR_REPO_COMMIT_MESSAGE": None,
        "APPVEYOR_REPO_COMMIT_AUTHOR": "",
    }

    with mock.patch("ddtrace.ext.ci.git.extract_git_metadata") as mock_git_data:
        mock_git_data.return_value = {
            "git.repository_url": "https://github.com/appveyor-repo-name.git",
            "git.commit.message": "this is the correct commit message",
            "git.commit.author.name": "John Doe",
            "git.commit.author.email": "jdoe@gmail.com",
            "git.commit.author.date": "2021-01-19T09:24:53Z",
            "git.commit.committer.name": "Jane Doe",
            "git.commit.committer.email": "jane_doe@gmail.com",
            "git.commit.committer.date": "2021-01-19T09:24:53Z",
        }
        extracted_tags = ci.tags(ci_provider_env)

    assert extracted_tags["git.repository_url"] == "https://github.com/appveyor-repo-name.git"
    assert extracted_tags["git.commit.message"] == "this is the correct commit message"
    assert extracted_tags["git.commit.author.name"] == "John Doe"
    assert extracted_tags["git.commit.author.email"] == "jdoe@gmail.com"
