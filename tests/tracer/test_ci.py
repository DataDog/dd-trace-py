from collections import Counter
import glob
import json
import os

import mock
import pytest

from ddtrace.ext import ci
from ddtrace.ext import git
from ddtrace.ext.ci import _filter_sensitive_info
from tests import utils


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
def git_repo_empty(tmpdir):
    yield utils.git_repo_empty(tmpdir)


@pytest.fixture
def git_repo(git_repo_empty):
    yield utils.git_repo(git_repo_empty)


@pytest.mark.parametrize("name,environment,tags", _ci_fixtures())
def test_ci_providers(monkeypatch, name, environment, tags):
    """Make sure all provided environment variables from each CI provider are tagged correctly."""
    _updateenv(monkeypatch, environment)
    extracted_tags = ci.tags(environment)
    for key, value in tags.items():
        if key == ci.NODE_LABELS:
            assert Counter(json.loads(extracted_tags[key])) == Counter(json.loads(value))
        elif key == ci._CI_ENV_VARS:
            assert json.loads(extracted_tags[key]) == json.loads(value)
        else:
            assert extracted_tags[key] == value, "wrong tags in {0} for {1}".format(name, environment)


def test_git_extract_user_info(git_repo):
    """Make sure that git commit author/committer name, email, and date are extracted and tagged correctly."""
    expected_author = ("John Doe", "john@doe.com", "2021-01-19T09:24:53-0400")
    expected_committer = ("Jane Doe", "jane@doe.com", "2021-01-20T04:37:21-0400")
    extracted_users = git.extract_user_info(cwd=git_repo)

    assert extracted_users["author"] == expected_author
    assert extracted_users["committer"] == expected_committer


def test_git_extract_user_info_with_commas():
    with mock.patch(
        "ddtrace.ext.git._git_subprocess_cmd",
        return_value="Do, Jo|||jo@do.com|||2021-01-19T09:24:53-0400|||Do, Ja|||ja@do.com|||2021-01-20T04:37:21-0400",
    ):
        extracted_users = git.extract_user_info()
        assert extracted_users["author"] == ("Do, Jo", "jo@do.com", "2021-01-19T09:24:53-0400")
        assert extracted_users["committer"] == ("Do, Ja", "ja@do.com", "2021-01-20T04:37:21-0400")


def test_git_extract_user_info_error(git_repo_empty):
    """On error, the author/committer tags should not be extracted, and should internally raise an error."""
    with pytest.raises(ValueError):
        git.extract_user_info(cwd=git_repo_empty)


def test_git_extract_repository_url(git_repo):
    """Make sure that the git repository url is extracted properly."""
    expected_repository_url = "git@github.com:test-repo-url.git"
    assert git.extract_repository_url(cwd=git_repo) == expected_repository_url


def test_git_filter_repository_url_valid():
    """Make sure that valid git repository urls are not filtered."""
    valid_url_1 = "https://github.com/DataDog/dd-trace-py.git"
    valid_url_2 = "git@github.com:DataDog/dd-trace-py.git"
    valid_url_3 = "ssh://github.com/Datadog/dd-trace-py.git"

    assert _filter_sensitive_info(valid_url_1) == valid_url_1
    assert _filter_sensitive_info(valid_url_2) == valid_url_2
    assert _filter_sensitive_info(valid_url_3) == valid_url_3


def test_git_filter_repository_url_invalid():
    """Make sure that valid git repository urls are not filtered."""
    invalid_url_1 = "https://username:password@github.com/DataDog/dd-trace-py.git"
    invalid_url_2 = "https://username@github.com/DataDog/dd-trace-py.git"

    invalid_url_3 = "ssh://username:password@github.com/DataDog/dd-trace-py.git"
    invalid_url_4 = "ssh://username@github.com/DataDog/dd-trace-py.git"

    assert _filter_sensitive_info(invalid_url_1) == "https://github.com/DataDog/dd-trace-py.git"
    assert _filter_sensitive_info(invalid_url_2) == "https://github.com/DataDog/dd-trace-py.git"

    assert _filter_sensitive_info(invalid_url_3) == "ssh://github.com/DataDog/dd-trace-py.git"
    assert _filter_sensitive_info(invalid_url_4) == "ssh://github.com/DataDog/dd-trace-py.git"


def test_git_extract_repository_url_error(git_repo_empty):
    """On error, the repository url tag should not be extracted, and should internally raise an error."""
    with pytest.raises(ValueError):
        git.extract_repository_url(cwd=git_repo_empty)


def test_git_extract_commit_message(git_repo):
    """Make sure that the git commit message is extracted properly."""
    expected_msg = "this is a commit msg"
    assert git.extract_commit_message(cwd=git_repo) == expected_msg


def test_git_extract_commit_message_error(git_repo_empty):
    """On error, the commit message tag should not be extracted, and should internally raise an error."""
    with pytest.raises(ValueError):
        git.extract_commit_message(cwd=git_repo_empty)


def test_git_extract_workspace_path(git_repo):
    """Make sure that workspace path is correctly extracted."""
    assert git.extract_workspace_path(cwd=git_repo) == git_repo


def test_git_extract_workspace_path_error(tmpdir):
    """On error, workspace path should not be extracted, and should internally raise an error."""
    with pytest.raises(ValueError):
        git.extract_workspace_path(cwd=str(tmpdir))


def test_extract_git_metadata(git_repo):
    """Test that extract_git_metadata() sets all tags correctly."""
    with mock.patch("ddtrace.ext.git._set_safe_directory") as mock_git_set_safe_directory:
        extracted_tags = git.extract_git_metadata(cwd=git_repo)

    mock_git_set_safe_directory.assert_called()
    assert extracted_tags["git.repository_url"] == "git@github.com:test-repo-url.git"
    assert extracted_tags["git.commit.message"] == "this is a commit msg"
    assert extracted_tags["git.commit.author.name"] == "John Doe"
    assert extracted_tags["git.commit.author.email"] == "john@doe.com"
    assert extracted_tags["git.commit.author.date"] == "2021-01-19T09:24:53-0400"
    assert extracted_tags["git.commit.committer.name"] == "Jane Doe"
    assert extracted_tags["git.commit.committer.email"] == "jane@doe.com"
    assert extracted_tags["git.commit.committer.date"] == "2021-01-20T04:37:21-0400"
    assert extracted_tags["git.branch"] == "master"
    assert extracted_tags.get("git.commit.sha") is not None  # Commit hash will always vary, just ensure a value is set


def test_extract_git_user_provided_metadata_overwrites_ci(git_repo):
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
    extracted_tags = ci.tags(env=ci_env, cwd=git_repo)

    assert extracted_tags["git.repository_url"] == "https://github.com/user-repo-name.git"
    assert extracted_tags["git.commit.sha"] == "1234"
    assert extracted_tags["git.branch"] == "branch"
    assert extracted_tags["git.commit.message"] == "message"
    assert extracted_tags["git.commit.author.name"] == "author name"
    assert extracted_tags["git.commit.author.email"] == "author email"
    assert extracted_tags["git.commit.author.date"] == "author date"
    assert extracted_tags["git.commit.committer.name"] == "committer name"
    assert extracted_tags["git.commit.committer.email"] == "committer email"
    assert extracted_tags["git.commit.committer.date"] == "committer date"


def test_git_executable_not_found_error(git_repo_empty):
    """If git executable not available, should raise internally, log, and not extract any tags."""
    with mock.patch("ddtrace.ext.ci.git.log") as log:
        with mock.patch("ddtrace.ext.ci.git.subprocess.Popen") as mock_popen:
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
        "APPVEYOR_REPO_COMMIT_MESSAGE": "this is the correct commit message",
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

    extracted_tags = ci.tags(env=ci_provider_env, cwd=git_repo)

    assert extracted_tags["git.repository_url"] == "git@github.com:test-repo-url.git"
    assert extracted_tags["git.commit.message"] == "this is a commit msg"
    assert extracted_tags["git.commit.author.name"] == "John Doe"
    assert extracted_tags["git.commit.author.email"] == "john@doe.com"
    assert extracted_tags["ci.workspace_path"] == git_repo


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


def test_get_rev_list_no_args_git_ge_223(git_repo):
    with mock.patch(
        "ddtrace.ext.git._git_subprocess_cmd_with_details",
        return_value=git._GitSubprocessDetails("commithash1\ncommithash2\n", "", 10, 0),
    ) as mock_git_subprocess_cmd_with_details, mock.patch(
        "ddtrace.ext.git.extract_git_version", return_value=(2, 23, 0)
    ):
        assert git._get_rev_list(cwd=git_repo) == "commithash1\ncommithash2\n"
        mock_git_subprocess_cmd_with_details.assert_called_once_with(
            "rev-list",
            "--objects",
            "--filter=blob:none",
            '--since="1 month ago"',
            "--no-object-names",
            "HEAD",
            cwd=git_repo,
        )


def test_get_rev_list_git_lt_223(git_repo):
    with mock.patch(
        "ddtrace.ext.git._git_subprocess_cmd_with_details",
        return_value=git._GitSubprocessDetails("commithash1\ncommithash2\n", "", 10, 0),
    ) as mock_git_subprocess_cmd_with_details, mock.patch(
        "ddtrace.ext.git.extract_git_version", return_value=(2, 22, 0)
    ):
        assert (
            git._get_rev_list(
                excluded_commit_shas=["exclude1", "exclude2"],
                included_commit_shas=["include1", "include2"],
                cwd=git_repo,
            )
            == "commithash1\ncommithash2\n"
        )
        mock_git_subprocess_cmd_with_details.assert_called_once_with(
            "rev-list",
            "--objects",
            "--filter=blob:none",
            "HEAD",
            "^exclude1",
            "^exclude2",
            "include1",
            "include2",
            cwd=git_repo,
        )


def test_valid_git_version(git_repo):
    with mock.patch("ddtrace.ext.git._git_subprocess_cmd", return_value="git version 2.6.13 (Apple 256)"):
        assert git.extract_git_version(cwd=git_repo) == (2, 6, 13)
    with mock.patch("ddtrace.ext.git._git_subprocess_cmd", return_value="git version 2.6.13"):
        assert git.extract_git_version(cwd=git_repo) == (2, 6, 13)


def test_invalid_git_version(git_repo):
    with mock.patch("ddtrace.ext.git._git_subprocess_cmd", return_value="git version 2.6.13a (invalid)"):
        assert git.extract_git_version(cwd=git_repo) == (0, 0, 0)
    with mock.patch("ddtrace.ext.git._git_subprocess_cmd", return_value="git version (invalid)"):
        assert git.extract_git_version(cwd=git_repo) == (0, 0, 0)


def test_is_shallow_repository_true(git_repo):
    with mock.patch(
        "ddtrace.ext.git._git_subprocess_cmd_with_details", return_value=git._GitSubprocessDetails("true", "", 10, 0)
    ) as mock_git_subprocess_cmd_with_details:
        is_shallow, duration, returncode = git._is_shallow_repository_with_details(cwd=git_repo)
        assert is_shallow is True
        assert duration == 10
        assert returncode == 0
        mock_git_subprocess_cmd_with_details.assert_called_once_with(
            "rev-parse", "--is-shallow-repository", cwd=git_repo
        )


def test_is_shallow_repository_false(git_repo):
    with mock.patch(
        "ddtrace.ext.git._git_subprocess_cmd_with_details", return_value=git._GitSubprocessDetails("false", "", 10, 0)
    ) as mock_git_subprocess_cmd_with_details:
        is_shallow, duration, returncode = git._is_shallow_repository_with_details(cwd=git_repo)
        assert is_shallow is False
        assert duration == 10
        assert returncode == 0
        mock_git_subprocess_cmd_with_details.assert_called_once_with(
            "rev-parse", "--is-shallow-repository", cwd=git_repo
        )


def test_unshallow_repository_bare(git_repo):
    with mock.patch(
        "ddtrace.ext.git._git_subprocess_cmd_with_details", return_value=git._GitSubprocessDetails("", "", 10, 0)
    ) as mock_git_subprocess_cmd_with_details:
        git._unshallow_repository(cwd=git_repo)
        mock_git_subprocess_cmd_with_details.assert_called_once_with(
            "fetch",
            '--shallow-since="1 month ago"',
            "--update-shallow",
            "--filter=blob:none",
            "--recurse-submodules=no",
            cwd=git_repo,
        )


def test_unshallow_repository_bare_repo(git_repo):
    with mock.patch(
        "ddtrace.ext.git._git_subprocess_cmd_with_details", return_value=git._GitSubprocessDetails("", "", 10, 0)
    ) as mock_git_subprocess_cmd_with_details:
        git._unshallow_repository(cwd=git_repo, repo="myremote")
        mock_git_subprocess_cmd_with_details.assert_called_once_with(
            "fetch",
            '--shallow-since="1 month ago"',
            "--update-shallow",
            "--filter=blob:none",
            "--recurse-submodules=no",
            "myremote",
            cwd=git_repo,
        )


def test_unshallow_repository_bare_repo_refspec(git_repo):
    with mock.patch(
        "ddtrace.ext.git._git_subprocess_cmd_with_details", return_value=git._GitSubprocessDetails("", "", 10, 0)
    ) as mock_git_subprocess_cmd_with_details:
        git._unshallow_repository(cwd=git_repo, repo="myremote", refspec="mycommitshaaaaaaaaaaaa123")
        mock_git_subprocess_cmd_with_details.assert_called_once_with(
            "fetch",
            '--shallow-since="1 month ago"',
            "--update-shallow",
            "--filter=blob:none",
            "--recurse-submodules=no",
            "myremote",
            "mycommitshaaaaaaaaaaaa123",
            cwd=git_repo,
        )


def test_extract_clone_defaultremotename():
    with mock.patch(
        "ddtrace.ext.git._git_subprocess_cmd_with_details",
        return_value=git._GitSubprocessDetails("default_remote_name", "", 10, 0),
    ) as mock_git_subprocess_cmd_with_details:
        remote_name, _, duration, returncode = git._extract_clone_defaultremotename_with_details(cwd=git_repo)
        assert remote_name == "default_remote_name"
        assert duration == 10
        assert returncode == 0
        mock_git_subprocess_cmd_with_details.assert_called_once_with(
            "config", "--default", "origin", "--get", "clone.defaultRemoteName", cwd=git_repo
        )


def test_build_git_packfiles(git_repo):
    found_rand = found_idx = found_pack = False
    with git.build_git_packfiles("b3672ea5cbc584124728c48a443825d2940e0ddd\n", cwd=git_repo) as packfiles_path:
        assert packfiles_path
        parts = packfiles_path.split("/")
        directory = "/".join(parts[:-1])
        rand = parts[-1]
        assert os.path.isdir(directory)
        for filename in os.listdir(directory):
            if rand in filename:
                found_rand = True
                if filename.endswith(".idx"):
                    found_idx = True
                elif filename.endswith(".pack"):
                    found_pack = True
            if found_rand and found_idx and found_pack:
                break
        else:
            pytest.fail()
    assert not os.path.isdir(directory)


@mock.patch("ddtrace.ext.git._get_device_for_path", side_effect=[1, 2])
def test_build_git_packfiles_temp_dir_value_error(_temp_dir_mock, git_repo):
    found_rand = found_idx = found_pack = False
    with git.build_git_packfiles("b3672ea5cbc584124728c48a443825d2940e0ddd\n", cwd=git_repo) as packfiles_path:
        assert packfiles_path
        parts = packfiles_path.split("/")
        directory = "/".join(parts[:-1])
        rand = parts[-1]
        assert os.path.isdir(directory)
        for filename in os.listdir(directory):
            if rand in filename:
                found_rand = True
                if filename.endswith(".idx"):
                    found_idx = True
                elif filename.endswith(".pack"):
                    found_pack = True
            if found_rand and found_idx and found_pack:
                break
        else:
            pytest.fail()
    # CWD is not a temporary dir, so no deleted after using it.
    assert os.path.isdir(directory)
