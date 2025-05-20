from ddtrace.internal.ci_visibility.git_data import GitData
from ddtrace.internal.ci_visibility.git_data import get_git_data_from_tags
from ddtrace.ext import ci


def test_git_data_with_branch():
    tags = {
        ci.git.REPOSITORY_URL: "github.com/some-org/some-repo",
        ci.git.BRANCH: "some-branch",
        ci.git.COMMIT_SHA: "some-sha",
        ci.git.COMMIT_MESSAGE: "this is a message",
    }

    expected_git_data = GitData(
        repository_url="github.com/some-org/some-repo",
        branch="some-branch",
        commit_sha="some-sha",
        commit_message="this is a message"
    )

    assert get_git_data_from_tags(tags) == expected_git_data


def test_git_data_with_tag():
    tags = {
        ci.git.REPOSITORY_URL: "github.com/some-org/some-repo",
        ci.git.TAG: "v1.2.3",
        ci.git.COMMIT_SHA: "some-sha",
        ci.git.COMMIT_MESSAGE: "this is a message",
    }

    expected_git_data = GitData(
        repository_url="github.com/some-org/some-repo",
        branch="v1.2.3",
        commit_sha="some-sha",
        commit_message="this is a message"
    )

    assert get_git_data_from_tags(tags) == expected_git_data


def test_git_data_with_neither_branch_nor_tag():
    tags = {
        ci.git.REPOSITORY_URL: "github.com/some-org/some-repo",
        ci.git.COMMIT_SHA: "some-sha",
        ci.git.COMMIT_MESSAGE: "this is a message",
    }

    expected_git_data = GitData(
        repository_url="github.com/some-org/some-repo",
        branch=None,
        commit_sha="some-sha",
        commit_message="this is a message"
    )

    assert get_git_data_from_tags(tags) == expected_git_data
