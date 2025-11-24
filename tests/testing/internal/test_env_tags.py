from collections import Counter
import json
import os
from pathlib import Path
import typing as t
from unittest import mock

import pytest

from ddtestpy.internal.ci import CITag
from ddtestpy.internal.env_tags import get_env_tags
from ddtestpy.internal.git import Git
from ddtestpy.internal.git import GitTag
from ddtestpy.internal.git import get_git_tags_from_git_command
from ddtestpy.internal.utils import _filter_sensitive_info


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "ci"


def _ci_fixtures() -> t.Iterable[t.Tuple[str, int, t.Dict[str, str], t.Dict[str, str]]]:
    for filepath in FIXTURES_DIR.glob("*.json"):
        with open(filepath) as fp:
            for i, [env_vars, expected_tags] in enumerate(json.load(fp)):
                yield filepath.stem, i, env_vars, expected_tags


@pytest.mark.parametrize("name,i,environment,tags", _ci_fixtures())
def test_ci_providers(
    monkeypatch: pytest.MonkeyPatch, name: str, i: int, environment: t.Dict[str, str], tags: t.Dict[str, str]
) -> None:
    """Make sure all provided environment variables from each CI provider are tagged correctly."""
    monkeypatch.setattr(os, "environ", environment)

    with mock.patch("ddtestpy.internal.git.get_git_tags_from_git_command", return_value={}):
        extracted_tags = get_env_tags()

    for key, value in tags.items():
        if key == CITag.NODE_LABELS:
            assert Counter(json.loads(extracted_tags[key])) == Counter(json.loads(value))
        elif key == CITag._CI_ENV_VARS:
            assert json.loads(extracted_tags[key]) == json.loads(value)
        else:
            assert extracted_tags[key] == value, "wrong tags in {0} for {1}".format(name, environment)


def test_git_extract_user_info(monkeypatch: pytest.MonkeyPatch, git_repo: str) -> None:
    """Make sure that git commit author/committer name, email, and date are extracted and tagged correctly."""
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.chdir(git_repo)

    tags = get_env_tags()

    assert tags[GitTag.COMMIT_AUTHOR_NAME] == "John Doe"
    assert tags[GitTag.COMMIT_AUTHOR_EMAIL] == "john@doe.com"
    assert tags[GitTag.COMMIT_AUTHOR_DATE] == "2021-01-19T09:24:53-0400"
    assert tags[GitTag.COMMIT_COMMITTER_NAME] == "Jane Doe"
    assert tags[GitTag.COMMIT_COMMITTER_EMAIL] == "jane@doe.com"
    assert tags[GitTag.COMMIT_COMMITTER_DATE] == "2021-01-20T04:37:21-0400"


def test_git_extract_user_info_with_commas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", {})

    with mock.patch.object(
        Git,
        "_git_output",
        return_value="Do, Jo|||jo@do.com|||2021-01-19T09:24:53-0400|||Do, Ja|||ja@do.com|||2021-01-20T04:37:21-0400",
    ):
        tags = get_env_tags()

    assert tags[GitTag.COMMIT_AUTHOR_NAME] == "Do, Jo"
    assert tags[GitTag.COMMIT_AUTHOR_EMAIL] == "jo@do.com"
    assert tags[GitTag.COMMIT_AUTHOR_DATE] == "2021-01-19T09:24:53-0400"
    assert tags[GitTag.COMMIT_COMMITTER_NAME] == "Do, Ja"
    assert tags[GitTag.COMMIT_COMMITTER_EMAIL] == "ja@do.com"
    assert tags[GitTag.COMMIT_COMMITTER_DATE] == "2021-01-20T04:37:21-0400"


def test_git_extract_no_info_available(monkeypatch: pytest.MonkeyPatch, git_repo_empty: str) -> None:
    """When git data is not available, return no tags."""
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.chdir(git_repo_empty)

    tags = get_env_tags()
    assert tags == {CITag.WORKSPACE_PATH: git_repo_empty}  # We still get the workspace path from the current directory.


def test_git_extract_repository_url(monkeypatch: pytest.MonkeyPatch, git_repo: str) -> None:
    """Make sure that the git repository url is extracted properly."""
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.chdir(git_repo)

    expected_repository_url = "git@github.com:test-repo-url.git"
    tags = get_env_tags()

    assert tags[GitTag.REPOSITORY_URL] == expected_repository_url


def test_git_filter_repository_url_valid() -> None:
    """Make sure that git repository urls without sensitive data are not filtered."""
    valid_url_1 = "https://github.com/DataDog/dd-trace-py.git"
    valid_url_2 = "git@github.com:DataDog/dd-trace-py.git"
    valid_url_3 = "ssh://github.com/Datadog/dd-trace-py.git"

    assert _filter_sensitive_info(valid_url_1) == valid_url_1
    assert _filter_sensitive_info(valid_url_2) == valid_url_2
    assert _filter_sensitive_info(valid_url_3) == valid_url_3


def test_git_filter_repository_url_invalid() -> None:
    """Make sure that git repository urls with sensitive data are not filtered."""
    """Make sure that valid git repository urls are not filtered."""

    invalid_url_1 = "https://username:password@github.com/DataDog/dd-trace-py.git"
    invalid_url_2 = "https://username@github.com/DataDog/dd-trace-py.git"

    invalid_url_3 = "ssh://username:password@github.com/DataDog/dd-trace-py.git"
    invalid_url_4 = "ssh://username@github.com/DataDog/dd-trace-py.git"

    assert _filter_sensitive_info(invalid_url_1) == "https://github.com/DataDog/dd-trace-py.git"
    assert _filter_sensitive_info(invalid_url_2) == "https://github.com/DataDog/dd-trace-py.git"

    assert _filter_sensitive_info(invalid_url_3) == "ssh://github.com/DataDog/dd-trace-py.git"
    assert _filter_sensitive_info(invalid_url_4) == "ssh://github.com/DataDog/dd-trace-py.git"


def test_git_extract_commit_message(monkeypatch: pytest.MonkeyPatch, git_repo: str) -> None:
    """Make sure that the git commit message is extracted properly."""
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.chdir(git_repo)

    expected_msg = "this is a commit msg"

    tags = get_env_tags()

    assert tags[GitTag.COMMIT_MESSAGE] == expected_msg


def test_git_extract_workspace_path(monkeypatch: pytest.MonkeyPatch, git_repo: str) -> None:
    """Make sure that workspace path is correctly extracted."""
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.chdir(git_repo)

    tags = get_env_tags()
    assert tags[CITag.WORKSPACE_PATH] == git_repo


def test_git_extract_workspace_path_error(monkeypatch: pytest.MonkeyPatch, tmpdir: t.Any) -> None:
    """On a non-git folder, workspace path should be the current directory."""
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.chdir(str(tmpdir))

    tags = get_env_tags()
    assert tags[CITag.WORKSPACE_PATH] == str(tmpdir)


def test_git_extract_metadata(monkeypatch: pytest.MonkeyPatch, git_repo: str) -> None:
    """Make sure that all Git metadata is extracted and tagged correctly."""
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.chdir(git_repo)

    tags = get_env_tags()

    assert tags[GitTag.REPOSITORY_URL] == "git@github.com:test-repo-url.git"
    assert tags[GitTag.COMMIT_MESSAGE] == "this is a commit msg"
    assert tags[GitTag.COMMIT_AUTHOR_NAME] == "John Doe"
    assert tags[GitTag.COMMIT_AUTHOR_EMAIL] == "john@doe.com"
    assert tags[GitTag.COMMIT_AUTHOR_DATE] == "2021-01-19T09:24:53-0400"
    assert tags[GitTag.COMMIT_COMMITTER_NAME] == "Jane Doe"
    assert tags[GitTag.COMMIT_COMMITTER_EMAIL] == "jane@doe.com"
    assert tags[GitTag.COMMIT_COMMITTER_DATE] == "2021-01-20T04:37:21-0400"
    assert tags[GitTag.BRANCH] == "master"
    assert tags[GitTag.COMMIT_SHA] is not None  # Commit hash will always vary, just ensure a value is set


def test_extract_git_user_provided_metadata_overwrites_ci(monkeypatch: pytest.MonkeyPatch, git_repo: str) -> None:
    """Test that user-provided git metadata overwrites CI provided env vars."""
    ci_env = {
        "DD_GIT_REPOSITORY_URL": "https://github.com/user-repo-name.git",
        "DD_GIT_COMMIT_SHA": "1234",
        "DD_GIT_BRANCH": "branch",
        "DD_GIT_COMMIT_MESSAGE": "message",
        "DD_GIT_COMMIT_AUTHOR_NAME": "author name",
        "DD_GIT_COMMIT_AUTHOR_EMAIL": "author email",
        "DD_GIT_COMMIT_AUTHOR_DATE": "author date",
        "DD_GIT_COMMIT_COMMITTER_NAME": "committer name",
        "DD_GIT_COMMIT_COMMITTER_EMAIL": "committer email",
        "DD_GIT_COMMIT_COMMITTER_DATE": "committer date",
        "APPVEYOR": "true",
        "APPVEYOR_BUILD_FOLDER": "/foo/bar",
        "APPVEYOR_BUILD_ID": "appveyor-build-id",
        "APPVEYOR_BUILD_NUMBER": "appveyor-pipeline-number",
        "APPVEYOR_REPO_BRANCH": "master",
        "APPVEYOR_REPO_COMMIT": "appveyor-repo-commit",
        "APPVEYOR_REPO_NAME": "appveyor-repo-name",
        "APPVEYOR_REPO_PROVIDER": "github",
        "APPVEYOR_REPO_COMMIT_MESSAGE": "this is the correct commit message",
        "APPVEYOR_REPO_COMMIT_AUTHOR": "John Jack",
        "APPVEYOR_REPO_COMMIT_AUTHOR_EMAIL": "john@jack.com",
    }
    monkeypatch.setattr(os, "environ", ci_env)
    monkeypatch.chdir(git_repo)

    tags = get_env_tags()

    assert tags["git.repository_url"] == "https://github.com/user-repo-name.git"
    assert tags["git.commit.sha"] == "1234"
    assert tags["git.branch"] == "branch"
    assert tags["git.commit.message"] == "message"
    assert tags["git.commit.author.name"] == "author name"
    assert tags["git.commit.author.email"] == "author email"
    assert tags["git.commit.author.date"] == "author date"
    assert tags["git.commit.committer.name"] == "committer name"
    assert tags["git.commit.committer.email"] == "committer email"
    assert tags["git.commit.committer.date"] == "committer date"


def test_extract_git_head_commit_data(monkeypatch: pytest.MonkeyPatch, git_shallow_repo: t.Tuple[str, str]) -> None:
    git_repo, head_sha = git_shallow_repo
    github_sha = "abcd1234"

    github_event_path = f"{git_repo}/event.json"
    with open(github_event_path, "w") as f:
        json.dump({"pull_request": {"head": {"sha": head_sha}}}, f)

    ci_env = {
        "GITHUB_SHA": github_sha,
        "GITHUB_EVENT_PATH": github_event_path,
    }

    monkeypatch.setattr(os, "environ", ci_env)
    monkeypatch.chdir(git_repo)

    tags = get_env_tags()

    assert tags[GitTag.COMMIT_AUTHOR_NAME] == "John Doe"
    assert tags[GitTag.COMMIT_AUTHOR_EMAIL] == "john@doe.com"
    assert tags[GitTag.COMMIT_AUTHOR_DATE] == "2021-01-19T09:24:53-0400"
    assert tags[GitTag.COMMIT_COMMITTER_NAME] == "Jane Doe"
    assert tags[GitTag.COMMIT_COMMITTER_EMAIL] == "jane@doe.com"
    assert tags[GitTag.COMMIT_COMMITTER_DATE] == "2021-01-20T04:37:21-0400"
    assert tags[GitTag.COMMIT_MESSAGE] == "this is a commit msg"
    assert tags[GitTag.COMMIT_SHA] == github_sha

    assert tags[GitTag.COMMIT_HEAD_AUTHOR_NAME] == "John Doe"
    assert tags[GitTag.COMMIT_HEAD_AUTHOR_EMAIL] == "john@doe.com"
    assert tags[GitTag.COMMIT_HEAD_AUTHOR_DATE] == "2020-01-19T09:24:53-0400"
    assert tags[GitTag.COMMIT_HEAD_COMMITTER_NAME] == "Jane Doe"
    assert tags[GitTag.COMMIT_HEAD_COMMITTER_EMAIL] == "jane@doe.com"
    assert tags[GitTag.COMMIT_HEAD_COMMITTER_DATE] == "2020-01-20T04:37:21-0400"
    assert tags[GitTag.COMMIT_HEAD_MESSAGE] == "initial commit"
    assert tags[GitTag.COMMIT_HEAD_SHA] == head_sha


def test_git_executable_not_found_error(monkeypatch: pytest.MonkeyPatch, git_repo: str) -> None:
    """If git executable not available, should raise internally, log, and not extract any tags."""
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.chdir(git_repo)

    with mock.patch("ddtestpy.internal.git.log") as log:
        with mock.patch("shutil.which", return_value=None):
            tags = get_git_tags_from_git_command()

    assert log.warning.call_count == 1
    assert tags == {}


def test_ci_provider_tags_not_overwritten_by_git_executable(monkeypatch: pytest.MonkeyPatch, git_repo: str) -> None:
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
        "APPVEYOR_REPO_COMMIT_AUTHOR": "John Jack",
        "APPVEYOR_REPO_COMMIT_AUTHOR_EMAIL": "john@jack.com",
    }
    monkeypatch.setattr(os, "environ", ci_provider_env)
    monkeypatch.chdir(git_repo)

    tags = get_env_tags()

    assert tags["git.repository_url"] == "https://github.com/appveyor-repo-name.git"
    assert tags["git.commit.message"] == "this is the correct commit message"
    assert tags["git.commit.author.name"] == "John Jack"
    assert tags["git.commit.author.email"] == "john@jack.com"


def test_falsey_ci_provider_values_overwritten_by_git_executable(
    monkeypatch: pytest.MonkeyPatch, git_repo: str
) -> None:
    """If no or None or empty string values from CI provider env, should be overwritten by extracted git metadata."""
    ci_provider_env = {
        "APPVEYOR": "true",
        "APPVEYOR_BUILD_FOLDER": "",
        "APPVEYOR_BUILD_ID": "appveyor-build-id",
        "APPVEYOR_BUILD_NUMBER": "appveyor-pipeline-number",
        "APPVEYOR_REPO_BRANCH": "master",
        "APPVEYOR_REPO_COMMIT": "appveyor-repo-commit",
        "APPVEYOR_REPO_NAME": "appveyor-repo-name",
        "APPVEYOR_REPO_PROVIDER": "not-github",
        "APPVEYOR_REPO_COMMIT_MESSAGE": None,
        "APPVEYOR_REPO_COMMIT_AUTHOR": "",
    }

    monkeypatch.setattr(os, "environ", ci_provider_env)
    monkeypatch.chdir(git_repo)

    tags = get_env_tags()

    assert tags["git.repository_url"] == "git@github.com:test-repo-url.git"
    assert tags["git.commit.message"] == "this is a commit msg"
    assert tags["git.commit.author.name"] == "John Doe"
    assert tags["git.commit.author.email"] == "john@doe.com"
    assert tags["ci.workspace_path"] == git_repo
