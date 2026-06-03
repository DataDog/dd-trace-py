import pytest

from ddtrace.sourcecode._utils import normalize_repository_url


@pytest.mark.parametrize(
    "repository_url,expected",
    [
        # Supported schemes.
        (
            "http://github.com/user/project.git",
            "http://github.com/user/project",
        ),
        (
            "https://github.com/user/project.git",
            "https://github.com/user/project",
        ),
        (
            "git://github.com/user/project.git",
            "https://github.com/user/project",
        ),
        (
            "git@github.com:user/project.git",
            "https://github.com/user/project",
        ),
        (
            "ssh://git@github.com/user/project.git",
            "https://github.com/user/project",
        ),
        (
            "git://github.com/user/project.git/",
            "https://github.com/user/project",
        ),
        # No scheme but valid TLD.
        (
            "github.com/user/project",
            "https://github.com/user/project",
        ),
        # Subdomain preserved.
        (
            "http://www.github.com/user/project.git",
            "http://www.github.com/user/project",
        ),
        # Preserve port for HTTP/HTTPS schemes.
        (
            "http://github.com:8080/user/project.git",
            "http://github.com:8080/user/project",
        ),
        (
            "https://github.com:8080/user/project.git",
            "https://github.com:8080/user/project",
        ),
        # Do not preserve port otherwise.
        (
            "ssh://git@github.com:22/user/project.git",
            "https://github.com/user/project",
        ),
        # Strip credentials.
        (
            "https://gitlab-ci-token:12345AbcDFoo_qbcdef@gitlab.com/user/project.git",
            "https://gitlab.com/user/project",
        ),
        # Not supported.
        (
            "ftp:///path/to/repo.git/",
            "",
        ),
        (
            "/path/to/repo.git/",
            "",
        ),
        (
            "file:///path/to/repo.git/",
            "",
        ),
    ],
)
def test_normalize_repository_url(repository_url, expected):
    assert normalize_repository_url(repository_url) == expected
