import os
from unittest import mock

from ddtrace.sourcecode._utils import get_commit_sha
from ddtrace.sourcecode._utils import get_repository_url


class TestSourceCodeEnvVars:
    def test_get_commit_sha_uses_env_var_when_present(self):
        test_sha = "abc123def456"
        with mock.patch.dict(os.environ, {"DD_GIT_COMMIT_SHA": test_sha}):
            with mock.patch("ddtrace.sourcecode._utils._query_git") as mock_git:
                result = get_commit_sha()
                assert result == test_sha
                mock_git.assert_not_called()

    def test_get_commit_sha_calls_git_when_env_var_not_present(self):
        test_sha = "git_result_sha"
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("ddtrace.sourcecode._utils._query_git", return_value=test_sha) as mock_git:
                result = get_commit_sha()
                assert result == test_sha
                mock_git.assert_called_once_with(["rev-parse", "HEAD"])

    def test_get_commit_sha_calls_git_when_env_var_empty(self):
        test_sha = "git_result_sha"
        with mock.patch.dict(os.environ, {"DD_GIT_COMMIT_SHA": ""}):
            with mock.patch("ddtrace.sourcecode._utils._query_git", return_value=test_sha) as mock_git:
                result = get_commit_sha()
                assert result == test_sha
                mock_git.assert_called_once_with(["rev-parse", "HEAD"])

    def test_get_repository_url_uses_env_var_when_present(self):
        test_url = "https://github.com/user/repo.git"
        with mock.patch.dict(os.environ, {"DD_GIT_REPOSITORY_URL": test_url}):
            with mock.patch("ddtrace.sourcecode._utils._query_git") as mock_git:
                result = get_repository_url()
                assert result == test_url
                mock_git.assert_not_called()

    def test_get_repository_url_calls_git_when_env_var_not_present(self):
        test_url = "git_result_url"
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("ddtrace.sourcecode._utils._query_git", return_value=test_url) as mock_git:
                result = get_repository_url()
                assert result == test_url
                mock_git.assert_called_once_with(["config", "--get", "remote.origin.url"])

    def test_get_repository_url_calls_git_when_env_var_empty(self):
        test_url = "git_result_url"
        with mock.patch.dict(os.environ, {"DD_GIT_REPOSITORY_URL": ""}):
            with mock.patch("ddtrace.sourcecode._utils._query_git", return_value=test_url) as mock_git:
                result = get_repository_url()
                assert result == test_url
                mock_git.assert_called_once_with(["config", "--get", "remote.origin.url"])
