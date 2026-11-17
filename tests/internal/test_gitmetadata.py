import pytest


@pytest.mark.subprocess(
    env={
        "DD_TAGS": "git.commit.sha:12345,git.repository_url:github.com/user/repo",
    }
)
def test_gitmetadata_caching(monkeypatch):
    from ddtrace.internal import gitmetadata

    repository_url, commit_sha, main_package = gitmetadata.get_git_tags()
    assert commit_sha == "12345"
    assert repository_url == "github.com/user/repo"

    # set new values
    gitmetadata.config.repository_url = "github.com/user/repo2"
    gitmetadata.config.commit_sha = "54321"
    gitmetadata.config.main_package = "mypackage"
    repository_url, commit_sha, main_package = gitmetadata.get_git_tags()
    # must have old values
    assert commit_sha == "12345"
    assert repository_url == "github.com/user/repo"
