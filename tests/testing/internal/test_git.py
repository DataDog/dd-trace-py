"""Tests for ddtrace.testing.internal.git module."""

from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.git import Git
from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.git import GitUserInfo
from ddtrace.testing.internal.git import _GitSubprocessDetails
from ddtrace.testing.internal.git import get_git_head_tags_from_git_command
from ddtrace.testing.internal.git import get_git_tags_from_git_command
from ddtrace.testing.internal.telemetry import GitTelemetry


_LOCK_STDERR = "fatal: Unable to create '/repo/.git/shallow.lock': File exists."


class TestGitTag:
    """Tests for GitTag constants."""

    def test_git_tag_constants(self) -> None:
        """Test that GitTag constants are correctly defined."""
        assert GitTag.REPOSITORY_URL == "git.repository_url"
        assert GitTag.COMMIT_SHA == "git.commit.sha"
        assert GitTag.BRANCH == "git.branch"
        assert GitTag.COMMIT_MESSAGE == "git.commit.message"
        assert GitTag.COMMIT_AUTHOR_NAME == "git.commit.author.name"
        assert GitTag.COMMIT_AUTHOR_EMAIL == "git.commit.author.email"
        assert GitTag.COMMIT_AUTHOR_DATE == "git.commit.author.date"
        assert GitTag.COMMIT_COMMITTER_NAME == "git.commit.committer.name"
        assert GitTag.COMMIT_COMMITTER_EMAIL == "git.commit.committer.email"
        assert GitTag.COMMIT_COMMITTER_DATE == "git.commit.committer.date"

    def test_git_tag_constants_unique(self) -> None:
        """Test that all GitTag constants are unique."""
        constants = [
            GitTag.REPOSITORY_URL,
            GitTag.COMMIT_SHA,
            GitTag.BRANCH,
            GitTag.COMMIT_MESSAGE,
            GitTag.COMMIT_AUTHOR_NAME,
            GitTag.COMMIT_AUTHOR_EMAIL,
            GitTag.COMMIT_AUTHOR_DATE,
            GitTag.COMMIT_COMMITTER_NAME,
            GitTag.COMMIT_COMMITTER_EMAIL,
            GitTag.COMMIT_COMMITTER_DATE,
        ]

        # All constants should be unique
        assert len(constants) == len(set(constants)), "GitTag constants are not unique"


class TestGitSubprocessDetails:
    """Tests for _GitSubprocessDetails dataclass."""

    def test_git_subprocess_details_creation(self) -> None:
        """Test that _GitSubprocessDetails can be created with required fields."""
        details = _GitSubprocessDetails(stdout="output", stderr="error", return_code=0, elapsed_seconds=0.0)

        assert details.stdout == "output"
        assert details.stderr == "error"
        assert details.return_code == 0
        assert details.elapsed_seconds == 0.0


class TestGit:
    """Tests for Git class."""

    @patch("shutil.which")
    def test_git_init_with_cwd(self, mock_which: Mock) -> None:
        """Test Git initialization with custom working directory."""
        mock_which.return_value = "/usr/bin/git"

        git = Git(cwd="/custom/path")
        assert git.git_command == "/usr/bin/git"
        assert git.cwd == "/custom/path"

    @patch("shutil.which")
    def test_git_init_git_not_found(self, mock_which: Mock) -> None:
        """Test Git initialization when git command is not found."""
        mock_which.return_value = None

        with pytest.raises(RuntimeError, match="`git` command not found"):
            Git()

    @patch("shutil.which")
    def test_get_repository_url(self, mock_which: Mock) -> None:
        """Test get_repository_url method."""
        mock_which.return_value = "/usr/bin/git"

        git = Git()
        with patch.object(git, "_git_output", return_value="https://github.com/user/repo.git") as mock_git_output:
            result = git.get_repository_url()

        assert result == "https://github.com/user/repo.git"
        mock_git_output.assert_called_once_with(["ls-remote", "--get-url"], GitTelemetry.GET_REPOSITORY)

    @patch("shutil.which")
    def test_get_commit_sha(self, mock_which: Mock) -> None:
        """Test get_commit_sha method."""
        mock_which.return_value = "/usr/bin/git"

        git = Git()
        with patch.object(git, "_git_output", return_value="abc123def456") as mock_git_output:
            result = git.get_commit_sha()

        assert result == "abc123def456"
        mock_git_output.assert_called_once_with(["rev-parse", "HEAD"])

    @patch("shutil.which")
    def test_get_branch(self, mock_which: Mock) -> None:
        """Test get_branch method."""
        mock_which.return_value = "/usr/bin/git"

        git = Git()
        with patch.object(git, "_git_output", return_value="main") as mock_git_output:
            result = git.get_branch()

        assert result == "main"
        mock_git_output.assert_called_once_with(["rev-parse", "--abbrev-ref", "HEAD"], GitTelemetry.GET_BRANCH)

    @patch("shutil.which")
    def test_get_commit_message(self, mock_which: Mock) -> None:
        """Test get_commit_message method."""
        mock_which.return_value = "/usr/bin/git"

        git = Git()
        with patch.object(git, "_git_output", return_value="Initial commit") as mock_git_output:
            result = git.get_commit_message()

        assert result == "Initial commit"
        mock_git_output.assert_called_once_with(["show", "-s", "--format=%s"])

    @patch("shutil.which")
    def test_get_user_info_success(self, mock_which: Mock) -> None:
        """Test get_user_info method with valid output."""
        mock_which.return_value = "/usr/bin/git"

        git = Git()
        mock_output = (
            "John Doe|||john@example.com|||2023-01-01T12:00:00+0000|||"
            "Jane Committer|||jane@example.com|||2023-01-01T12:30:00+0000"
        )

        with patch.object(git, "_git_output", return_value=mock_output):
            result = git.get_user_info()

        expected = GitUserInfo(
            author_name="John Doe",
            author_email="john@example.com",
            author_date="2023-01-01T12:00:00+0000",
            committer_name="Jane Committer",
            committer_email="jane@example.com",
            committer_date="2023-01-01T12:30:00+0000",
        )
        assert result == expected

    @patch("shutil.which")
    def test_get_user_info_no_output(self, mock_which: Mock) -> None:
        """Test get_user_info method with no output."""
        mock_which.return_value = "/usr/bin/git"

        git = Git()
        with patch.object(git, "_git_output", return_value=""):
            result = git.get_user_info()

        assert result is None

    @patch("shutil.which")
    def test_get_workspace_path(self, mock_which: Mock) -> None:
        """Test get_workspace_path method."""
        mock_which.return_value = "/usr/bin/git"

        git = Git()
        with patch.object(git, "_git_output", return_value="/path/to/repo") as mock_git_output:
            result = git.get_workspace_path()

        assert result == "/path/to/repo"
        mock_git_output.assert_called_once_with(["rev-parse", "--show-toplevel"])

    @patch("shutil.which")
    def test_get_latest_commits_success(self, mock_which: Mock) -> None:
        """Test get_latest_commits method with commits."""
        mock_which.return_value = "/usr/bin/git"

        git = Git()
        mock_output = "abc123\ndef456\nghi789"

        with patch.object(git, "_git_output", return_value=mock_output) as mock_git_output:
            result = git.get_latest_commits()

        assert result == ["abc123", "def456", "ghi789"]
        mock_git_output.assert_called_once_with(
            ["log", "--format=%H", "-n", "1000", '--since="1 month ago"'], GitTelemetry.GET_LOCAL_COMMITS
        )

    @patch("shutil.which")
    def test_get_latest_commits_no_output(self, mock_which: Mock) -> None:
        """Test get_latest_commits method with no commits."""
        mock_which.return_value = "/usr/bin/git"

        git = Git()
        with patch.object(git, "_git_output", return_value=""):
            result = git.get_latest_commits()

        assert result == []

    @patch("shutil.which")
    def test_get_filtered_revisions(self, mock_which: Mock) -> None:
        """Test get_filtered_revisions method."""
        mock_which.return_value = "/usr/bin/git"

        git = Git()
        mock_output = "commit1\ncommit2\ncommit3"
        excluded = ["exclude1", "exclude2"]
        included = ["include1"]

        with patch.object(git, "_git_output", return_value=mock_output) as mock_git_output:
            result = git.get_filtered_revisions(excluded, included)

        assert result == ["commit1", "commit2", "commit3"]
        mock_git_output.assert_called_once_with(
            [
                "rev-list",
                "--objects",
                "--filter=blob:none",
                '--since="1 month ago"',
                "--no-object-names",
                "HEAD",
                "^exclude1",
                "^exclude2",
                "include1",
            ],
            GitTelemetry.GET_OBJECTS,
        )


class TestGetGitTags:
    """Tests for get_git_tags_from_git_command function."""

    @patch("ddtrace.testing.internal.git.Git")
    def test_get_git_tags_success(self, mock_git_class: Mock) -> None:
        """Test get_git_tags_from_git_command with successful Git operations."""
        mock_git = Mock()
        mock_git.get_repository_url.return_value = "https://github.com/user/repo.git"
        mock_git.get_commit_sha.return_value = "abc123"
        mock_git.get_branch.return_value = "main"
        mock_git.get_commit_message.return_value = "Test commit"
        mock_git.get_user_info.return_value = GitUserInfo(
            author_name="John Doe",
            author_email="john@example.com",
            author_date="2023-01-01T12:00:00+0000",
            committer_name="Jane Committer",
            committer_email="jane@example.com",
            committer_date="2023-01-01T12:30:00+0000",
        )
        mock_git_class.return_value = mock_git

        result = get_git_tags_from_git_command()

        expected = {
            GitTag.REPOSITORY_URL: "https://github.com/user/repo.git",
            GitTag.COMMIT_SHA: "abc123",
            GitTag.BRANCH: "main",
            GitTag.COMMIT_MESSAGE: "Test commit",
            GitTag.COMMIT_AUTHOR_NAME: "John Doe",
            GitTag.COMMIT_AUTHOR_EMAIL: "john@example.com",
            GitTag.COMMIT_AUTHOR_DATE: "2023-01-01T12:00:00+0000",
            GitTag.COMMIT_COMMITTER_NAME: "Jane Committer",
            GitTag.COMMIT_COMMITTER_EMAIL: "jane@example.com",
            GitTag.COMMIT_COMMITTER_DATE: "2023-01-01T12:30:00+0000",
        }
        assert result == expected

    @patch("ddtrace.testing.internal.git.Git")
    @patch("ddtrace.testing.internal.git.log")
    def test_get_git_tags_git_not_available(self, mock_log: Mock, mock_git_class: Mock) -> None:
        """Test get_git_tags_from_git_command when Git is not available."""
        mock_git_class.side_effect = RuntimeError("git command not found")

        result = get_git_tags_from_git_command()

        assert result == {}
        mock_log.warning.assert_called_once_with("Error getting git data: %s", mock_git_class.side_effect)

    @patch("ddtrace.testing.internal.git.Git")
    def test_get_git_head_tags_success(self, mock_git_class: Mock) -> None:
        """Test get_git_head_tags_from_git_command with successful Git operations."""
        mock_parent_user = GitUserInfo(
            author_name="Parent Doe",
            author_email="parent@example.com",
            author_date="2020-01-01T12:00:00+0000",
            committer_name="Parent Committer",
            committer_email="parent@committer.com",
            committer_date="2020-01-01T12:30:00+0000",
        )

        mock_user = GitUserInfo(
            author_name="John Doe",
            author_email="john@example.com",
            author_date="2023-01-01T12:00:00+0000",
            committer_name="Jane Committer",
            committer_email="jane@example.com",
            committer_date="2023-01-01T12:30:00+0000",
        )

        mock_git = Mock()
        mock_git.get_repository_url.return_value = "https://github.com/user/repo.git"
        mock_git.get_commit_sha.return_value = "abc123"
        mock_git.get_branch.return_value = "main"
        mock_git.get_commit_message.side_effect = lambda sha=None: "Parent commit" if sha else "Test commit"
        mock_git.get_user_info.side_effect = lambda sha=None: mock_parent_user if sha else mock_user
        mock_git_class.return_value = mock_git

        result = get_git_head_tags_from_git_command("parent-sha")

        expected = {
            GitTag.COMMIT_HEAD_MESSAGE: "Parent commit",
            GitTag.COMMIT_HEAD_AUTHOR_NAME: "Parent Doe",
            GitTag.COMMIT_HEAD_AUTHOR_EMAIL: "parent@example.com",
            GitTag.COMMIT_HEAD_AUTHOR_DATE: "2020-01-01T12:00:00+0000",
            GitTag.COMMIT_HEAD_COMMITTER_NAME: "Parent Committer",
            GitTag.COMMIT_HEAD_COMMITTER_EMAIL: "parent@committer.com",
            GitTag.COMMIT_HEAD_COMMITTER_DATE: "2020-01-01T12:30:00+0000",
        }
        assert result == expected

    @patch("ddtrace.testing.internal.git.Git")
    def test_get_git_head_tags_with_no_user_info_available(self, mock_git_class: Mock) -> None:
        """Test get_git_head_tags_from_git_command with no user info available."""
        mock_git = Mock()
        mock_git.get_repository_url.return_value = "https://github.com/user/repo.git"
        mock_git.get_commit_sha.return_value = "abc123"
        mock_git.get_branch.return_value = "main"
        mock_git.get_commit_message.side_effect = lambda sha=None: "Parent commit" if sha else "Test commit"
        mock_git.get_user_info.return_value = None
        mock_git_class.return_value = mock_git

        result = get_git_head_tags_from_git_command("parent-sha")

        expected = {
            GitTag.COMMIT_HEAD_MESSAGE: "Parent commit",
        }
        assert result == expected


class TestGitUnshallow:
    """Tests for git unshallow logic."""

    @pytest.mark.parametrize("return_code", [0, 1])
    def test_git_unshallow_repository(self, return_code: int) -> None:
        with (
            patch(
                "ddtrace.testing.internal.git.Git._call_git",
                return_value=_GitSubprocessDetails(stdout="", stderr="", return_code=return_code, elapsed_seconds=0.0),
            ) as call_git_mock,
            patch("ddtrace.testing.internal.git.Git.get_remote_name", return_value="some-remote"),
        ):
            result = Git().unshallow_repository("some-sha")

        assert result.return_code == return_code

        assert call_git_mock.call_count == 1
        git_command = call_git_mock.call_args[0][0]
        assert git_command == [
            "fetch",
            '--shallow-since="1 month ago"',
            "--update-shallow",
            "--filter=blob:none",
            "--recurse-submodules=no",
            "--no-tags",
            "some-remote",
            "some-sha",
        ]

    @pytest.mark.parametrize("return_code", [0, 1])
    def test_git_unshallow_repository_parent_only(self, return_code: int) -> None:
        with (
            patch(
                "ddtrace.testing.internal.git.Git._call_git",
                return_value=_GitSubprocessDetails(stdout="", stderr="", return_code=return_code, elapsed_seconds=0.0),
            ) as call_git_mock,
            patch("ddtrace.testing.internal.git.Git.get_remote_name", return_value="some-remote"),
        ):
            result = Git().unshallow_repository(parent_only=True)

        assert result.return_code == return_code

        assert call_git_mock.call_count == 1
        git_command = call_git_mock.call_args[0][0]
        assert git_command == [
            "fetch",
            "--deepen=1",
            "--update-shallow",
            "--filter=blob:none",
            "--recurse-submodules=no",
            "--no-tags",
            "some-remote",
        ]

    @pytest.mark.parametrize("return_code", [0, 1])
    def test_git_unshallow_repository_to_local_head(self, return_code: int) -> None:
        with (
            patch(
                "ddtrace.testing.internal.git.Git._call_git",
                return_value=_GitSubprocessDetails(stdout="", stderr="", return_code=return_code, elapsed_seconds=0.0),
            ) as call_git_mock,
            patch("ddtrace.testing.internal.git.Git.get_remote_name", return_value="some-remote"),
            patch("ddtrace.testing.internal.git.Git.get_commit_sha", return_value="head-sha"),
        ):
            result = Git().unshallow_repository_to_local_head()

        assert result.return_code == return_code

        assert call_git_mock.call_count == 1
        git_command = call_git_mock.call_args[0][0]
        assert git_command == [
            "fetch",
            '--shallow-since="1 month ago"',
            "--update-shallow",
            "--filter=blob:none",
            "--recurse-submodules=no",
            "--no-tags",
            "some-remote",
            "head-sha",
        ]

    @pytest.mark.parametrize("return_code", [0, 1])
    def test_git_unshallow_repository_to_upstream(self, return_code: int) -> None:
        with (
            patch(
                "ddtrace.testing.internal.git.Git._call_git",
                return_value=_GitSubprocessDetails(stdout="", stderr="", return_code=return_code, elapsed_seconds=0.0),
            ) as call_git_mock,
            patch("ddtrace.testing.internal.git.Git.get_remote_name", return_value="some-remote"),
            patch(
                "ddtrace.testing.internal.git.Git.get_upstream_sha",
                return_value=_GitSubprocessDetails(
                    stdout="upstream-sha", stderr="", return_code=0, elapsed_seconds=0.0
                ),
            ),
        ):
            result = Git().unshallow_repository_to_upstream()

        assert result.return_code == return_code

        assert call_git_mock.call_count == 1
        git_command = call_git_mock.call_args[0][0]
        assert git_command == [
            "fetch",
            '--shallow-since="1 month ago"',
            "--update-shallow",
            "--filter=blob:none",
            "--recurse-submodules=no",
            "--no-tags",
            "some-remote",
            "upstream-sha",
        ]

    def test_git_try_all_unshallow_methods_1st_suceeds(self) -> None:
        call_git_results = [
            _GitSubprocessDetails(stdout="", stderr="", return_code=0, elapsed_seconds=0.0),
        ]

        with (
            patch("ddtrace.testing.internal.git.Git._call_git", side_effect=call_git_results) as call_git_mock,
            patch("ddtrace.testing.internal.git.Git.get_remote_name", return_value="some-remote"),
            patch("ddtrace.testing.internal.git.Git.get_commit_sha", return_value="head-sha"),
            patch(
                "ddtrace.testing.internal.git.Git.get_upstream_sha",
                return_value=_GitSubprocessDetails(
                    stdout="upstream-sha", stderr="", return_code=0, elapsed_seconds=0.0
                ),
            ),
        ):
            result = Git().try_all_unshallow_repository_methods()

        assert result

        git_commands = [call_args[0][0] for call_args in call_git_mock.call_args_list]
        assert git_commands == [
            [
                "fetch",
                '--shallow-since="1 month ago"',
                "--update-shallow",
                "--filter=blob:none",
                "--recurse-submodules=no",
                "--no-tags",
                "some-remote",
                "head-sha",
            ]
        ]

    def test_git_try_all_unshallow_methods_2nd_suceeds(self) -> None:
        call_git_results = [
            _GitSubprocessDetails(stdout="", stderr="", return_code=1, elapsed_seconds=0.0),
            _GitSubprocessDetails(stdout="", stderr="", return_code=0, elapsed_seconds=0.0),
        ]

        with (
            patch("ddtrace.testing.internal.git.Git._call_git", side_effect=call_git_results) as call_git_mock,
            patch("ddtrace.testing.internal.git.Git.get_remote_name", return_value="some-remote"),
            patch("ddtrace.testing.internal.git.Git.get_commit_sha", return_value="head-sha"),
            patch(
                "ddtrace.testing.internal.git.Git.get_upstream_sha",
                return_value=_GitSubprocessDetails(
                    stdout="upstream-sha", stderr="", return_code=0, elapsed_seconds=0.0
                ),
            ),
        ):
            result = Git().try_all_unshallow_repository_methods()

        assert result

        git_commands = [call_args[0][0] for call_args in call_git_mock.call_args_list]
        assert git_commands == [
            [
                "fetch",
                '--shallow-since="1 month ago"',
                "--update-shallow",
                "--filter=blob:none",
                "--recurse-submodules=no",
                "--no-tags",
                "some-remote",
                "head-sha",
            ],
            [
                "fetch",
                '--shallow-since="1 month ago"',
                "--update-shallow",
                "--filter=blob:none",
                "--recurse-submodules=no",
                "--no-tags",
                "some-remote",
                "upstream-sha",
            ],
        ]

    def test_git_try_all_unshallow_methods_3rd_suceeds(self) -> None:
        call_git_results = [
            _GitSubprocessDetails(stdout="", stderr="", return_code=1, elapsed_seconds=0.0),
            _GitSubprocessDetails(stdout="", stderr="", return_code=1, elapsed_seconds=0.0),
            _GitSubprocessDetails(stdout="", stderr="", return_code=0, elapsed_seconds=0.0),
        ]

        with (
            patch("ddtrace.testing.internal.git.Git._call_git", side_effect=call_git_results) as call_git_mock,
            patch("ddtrace.testing.internal.git.Git.get_remote_name", return_value="some-remote"),
            patch("ddtrace.testing.internal.git.Git.get_commit_sha", return_value="head-sha"),
            patch(
                "ddtrace.testing.internal.git.Git.get_upstream_sha",
                return_value=_GitSubprocessDetails(
                    stdout="upstream-sha", stderr="", return_code=0, elapsed_seconds=0.0
                ),
            ),
        ):
            result = Git().try_all_unshallow_repository_methods()

        assert result

        git_commands = [call_args[0][0] for call_args in call_git_mock.call_args_list]
        assert git_commands == [
            [
                "fetch",
                '--shallow-since="1 month ago"',
                "--update-shallow",
                "--filter=blob:none",
                "--recurse-submodules=no",
                "--no-tags",
                "some-remote",
                "head-sha",
            ],
            [
                "fetch",
                '--shallow-since="1 month ago"',
                "--update-shallow",
                "--filter=blob:none",
                "--recurse-submodules=no",
                "--no-tags",
                "some-remote",
                "upstream-sha",
            ],
            [
                "fetch",
                '--shallow-since="1 month ago"',
                "--update-shallow",
                "--filter=blob:none",
                "--recurse-submodules=no",
                "--no-tags",
                "some-remote",
            ],
        ]

    def test_git_try_all_unshallow_methods_all_fail(self) -> None:
        call_git_results = [
            _GitSubprocessDetails(stdout="", stderr="", return_code=1, elapsed_seconds=0.0),
            _GitSubprocessDetails(stdout="", stderr="", return_code=1, elapsed_seconds=0.0),
            _GitSubprocessDetails(stdout="", stderr="", return_code=1, elapsed_seconds=0.0),
        ]

        with (
            patch("ddtrace.testing.internal.git.Git._call_git", side_effect=call_git_results) as call_git_mock,
            patch("ddtrace.testing.internal.git.Git.get_remote_name", return_value="some-remote"),
            patch("ddtrace.testing.internal.git.Git.get_commit_sha", return_value="head-sha"),
            patch(
                "ddtrace.testing.internal.git.Git.get_upstream_sha",
                return_value=_GitSubprocessDetails(
                    stdout="upstream-sha", stderr="", return_code=0, elapsed_seconds=0.0
                ),
            ),
        ):
            result = Git().try_all_unshallow_repository_methods()

        assert not result

        git_commands = [call_args[0][0] for call_args in call_git_mock.call_args_list]
        assert git_commands == [
            [
                "fetch",
                '--shallow-since="1 month ago"',
                "--update-shallow",
                "--filter=blob:none",
                "--recurse-submodules=no",
                "--no-tags",
                "some-remote",
                "head-sha",
            ],
            [
                "fetch",
                '--shallow-since="1 month ago"',
                "--update-shallow",
                "--filter=blob:none",
                "--recurse-submodules=no",
                "--no-tags",
                "some-remote",
                "upstream-sha",
            ],
            [
                "fetch",
                '--shallow-since="1 month ago"',
                "--update-shallow",
                "--filter=blob:none",
                "--recurse-submodules=no",
                "--no-tags",
                "some-remote",
            ],
        ]


class TestGitLockRetry:
    """Tests for _call_git_with_lock_retry and the commands that use it."""

    @patch("shutil.which")
    def test_no_retry_on_success(self, mock_which: Mock) -> None:
        """A successful first call returns immediately without retrying."""
        mock_which.return_value = "/usr/bin/git"
        success = _GitSubprocessDetails(stdout="abc123", stderr="", return_code=0, elapsed_seconds=0.0)

        with (
            patch("ddtrace.testing.internal.git.Git._call_git", return_value=success) as mock_call,
            patch("time.sleep") as mock_sleep,
        ):
            result = Git()._call_git_with_lock_retry(["merge-base", "sha1", "sha2"])

        assert result.return_code == 0
        assert result.stdout == "abc123"
        mock_call.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("shutil.which")
    def test_no_retry_on_non_lock_error(self, mock_which: Mock) -> None:
        """A non-lock error is returned immediately without retrying."""
        mock_which.return_value = "/usr/bin/git"
        failure = _GitSubprocessDetails(
            stdout="", stderr="fatal: not a git repository", return_code=128, elapsed_seconds=0.0
        )

        with (
            patch("ddtrace.testing.internal.git.Git._call_git", return_value=failure) as mock_call,
            patch("time.sleep") as mock_sleep,
        ):
            result = Git()._call_git_with_lock_retry(["rev-parse", "HEAD"])

        assert result.return_code == 128
        mock_call.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("shutil.which")
    def test_retry_on_lock_error_then_succeed(self, mock_which: Mock) -> None:
        """A lock-contention failure is retried and succeeds on the next attempt."""
        mock_which.return_value = "/usr/bin/git"
        lock_failure = _GitSubprocessDetails(stdout="", stderr=_LOCK_STDERR, return_code=128, elapsed_seconds=0.0)
        success = _GitSubprocessDetails(stdout="", stderr="", return_code=0, elapsed_seconds=0.0)

        with (
            patch("ddtrace.testing.internal.git.Git._call_git", side_effect=[lock_failure, success]) as mock_call,
            patch("time.sleep") as mock_sleep,
        ):
            result = Git()._call_git_with_lock_retry(["fetch", "--update-shallow"])

        assert result.return_code == 0
        assert mock_call.call_count == 2
        mock_sleep.assert_called_once()

    @patch("shutil.which")
    def test_retry_exhausted_returns_last_failure(self, mock_which: Mock) -> None:
        """After _LOCK_MAX_RETRIES attempts the final failure result is returned."""
        mock_which.return_value = "/usr/bin/git"
        lock_failure = _GitSubprocessDetails(stdout="", stderr=_LOCK_STDERR, return_code=128, elapsed_seconds=0.0)

        import ddtrace.testing.internal.git as git_module

        with (
            patch(
                "ddtrace.testing.internal.git.Git._call_git",
                side_effect=[lock_failure] * (git_module._LOCK_MAX_RETRIES + 1),
            ) as mock_call,
            patch("time.sleep"),
        ):
            result = Git()._call_git_with_lock_retry(["fetch", "--update-shallow"])

        assert result.return_code == 128
        assert mock_call.call_count == git_module._LOCK_MAX_RETRIES + 1

    @patch("shutil.which")
    def test_unshallow_repository_retries_on_lock(self, mock_which: Mock) -> None:
        """unshallow_repository retries when git reports a shallow.lock contention."""
        mock_which.return_value = "/usr/bin/git"
        lock_failure = _GitSubprocessDetails(stdout="", stderr=_LOCK_STDERR, return_code=128, elapsed_seconds=0.0)
        success = _GitSubprocessDetails(stdout="", stderr="", return_code=0, elapsed_seconds=0.0)

        with (
            patch("ddtrace.testing.internal.git.Git._call_git", side_effect=[lock_failure, success]) as mock_call,
            patch("ddtrace.testing.internal.git.Git.get_remote_name", return_value="origin"),
            patch("time.sleep"),
        ):
            result = Git().unshallow_repository("some-sha")

        assert result.return_code == 0
        assert mock_call.call_count == 2

    @patch("shutil.which")
    def test_get_merge_base_success(self, mock_which: Mock) -> None:
        """get_merge_base returns the common ancestor SHA on success."""
        mock_which.return_value = "/usr/bin/git"
        success = _GitSubprocessDetails(stdout="deadbeef", stderr="", return_code=0, elapsed_seconds=0.0)

        with patch("ddtrace.testing.internal.git.Git._call_git", return_value=success):
            result = Git().get_merge_base("sha1", "sha2")

        assert result == "deadbeef"

    @patch("shutil.which")
    def test_get_merge_base_logs_warning_on_failure(self, mock_which: Mock) -> None:
        """get_merge_base logs a warning including both SHAs when it fails."""
        mock_which.return_value = "/usr/bin/git"
        failure = _GitSubprocessDetails(
            stdout="", stderr="fatal: Not a valid object name sha1", return_code=128, elapsed_seconds=0.0
        )

        with (
            patch("ddtrace.testing.internal.git.Git._call_git", return_value=failure),
            patch("ddtrace.testing.internal.git.log") as mock_log,
        ):
            result = Git().get_merge_base("sha1", "sha2")

        assert result == ""
        mock_log.warning.assert_called_once()
        warning_args = mock_log.warning.call_args[0]
        assert "sha1" in warning_args
        assert "sha2" in warning_args
