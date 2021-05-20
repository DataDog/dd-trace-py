import glob
import json
import os

import mock
import pytest

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


def test_git_extract_info_author():
    """Make sure that git commit author name, email, and date are extracted and tagged correctly."""
    expected_author = ("John Doe", "john@doe.com", "2021-02-19T08:24:53Z")
    mock_author_output = b"John Doe,john@doe.com,2021-02-19T08:24:53Z"

    with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_subprocess_popen:
        mock_subprocess_popen.return_value.returncode = 0
        mock_subprocess_popen.return_value.communicate.return_value = (mock_author_output, b"")
        extracted_author = git.extract_git_info(author=True)
        assert mock_subprocess_popen.call_args[0][0] == [
            "git",
            "show",
            "-s",
            "--format=%an,%ae,%ad",
            "--date=format:%Y-%m-%dT%H:%M:%S%z",
        ]
        extracted_tags = ci.tags({})

    assert extracted_author == expected_author
    assert extracted_tags["git.commit.author.name"] == expected_author[0]
    assert extracted_tags["git.commit.author.email"] == expected_author[1]
    assert extracted_tags["git.commit.author.date"] == expected_author[2]


def test_git_extract_info_committer():
    """Make sure that git commit committer name, email, and date are extracted and tagged correctly."""
    expected_committer = ("Jane Doe", "jane@doe.com", "2021-01-19T09:24:53Z")
    mock_committer_output = b"Jane Doe,jane@doe.com,2021-01-19T09:24:53Z"

    with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_subprocess_popen:
        mock_subprocess_popen.return_value.returncode = 0
        mock_subprocess_popen.return_value.communicate.return_value = (mock_committer_output, b"")
        extracted_committer = git.extract_git_info(author=False)
        assert mock_subprocess_popen.call_args[0][0] == [
            "git",
            "show",
            "-s",
            "--format=%cn,%ce,%cd",
            "--date=format:%Y-%m-%dT%H:%M:%S%z",
        ]
        extracted_tags = ci.tags({})

    assert extracted_committer == expected_committer
    assert extracted_tags["git.commit.committer.name"] == expected_committer[0]
    assert extracted_tags["git.commit.committer.email"] == expected_committer[1]
    assert extracted_tags["git.commit.committer.date"] == expected_committer[2]


def test_git_extract_error():
    """On error, the author/committer tags should be empty strings."""
    with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_subprocess_popen:
        mock_subprocess_popen.return_value.returncode = -1
        mock_subprocess_popen.return_value.communicate.return_value = (b"", b"")
        extracted_committer = git.extract_git_info(author=False)
        extracted_tags = ci.tags({})

    assert extracted_committer == ("", "", "")
    assert extracted_tags["git.commit.committer.name"] == ""
    assert extracted_tags["git.commit.committer.email"] == ""
    assert extracted_tags["git.commit.committer.date"] == ""
