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


def test_extract_git_metadata():
    """Test that extract_git_metadata() sets all tags correctly."""

    def side_effect(author):
        if author:
            return "John Doe", "john@doe.com", "2021-02-19T08:24:53Z"
        return "Jane Doe", "jane@doe.com", "2021-01-19T09:24:53Z"

    expected_tags = {
        git.REPOSITORY_URL: "https://github.com/scope-demo/scopeagent-reference-springboot2.git",
        git.COMMIT_MESSAGE: "Update README.md",
        git.COMMIT_AUTHOR_NAME: "John Doe",
        git.COMMIT_AUTHOR_EMAIL: "john@doe.com",
        git.COMMIT_AUTHOR_DATE: "2021-02-19T08:24:53Z",
        git.COMMIT_COMMITTER_NAME: "Jane Doe",
        git.COMMIT_COMMITTER_EMAIL: "jane@doe.com",
        git.COMMIT_COMMITTER_DATE: "2021-01-19T09:24:53Z",
    }

    with mock.patch("ddtrace.ext.git.extract_repository_url") as extract_repo_url:
        with mock.patch("ddtrace.ext.git.extract_commit_message") as extract_commit_message:
            with mock.patch("ddtrace.ext.git.extract_user_info") as extract_user_info:
                extract_repo_url.return_value = "https://github.com/scope-demo/scopeagent-reference-springboot2.git"
                extract_commit_message.return_value = "Update README.md"
                extract_user_info.side_effect = side_effect
                tags = git.extract_git_metadata()

    assert tags == expected_tags


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
