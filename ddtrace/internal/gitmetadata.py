import typing  # noqa:F401

from ddtrace.ext.ci import _filter_sensitive_info
from ddtrace.ext.git import COMMIT_SHA
from ddtrace.ext.git import MAIN_PACKAGE
from ddtrace.ext.git import REPOSITORY_URL
from ddtrace.internal.logger import get_logger
from ddtrace.internal.packages import get_distribution
from ddtrace.internal.utils import formats
from ddtrace.settings._core import DDConfig


_GITMETADATA_TAGS = None  # type: typing.Optional[typing.Tuple[str, str, str]]

log = get_logger(__name__)


class GitMetadataConfig(DDConfig):
    __prefix__ = "dd"

    # DD_TRACE_GIT_METADATA_ENABLED
    enabled = DDConfig.var(bool, "trace_git_metadata_enabled", default=True)

    # DD_GIT_REPOSITORY_URL
    repository_url = DDConfig.var(str, "git_repository_url", default="")

    # DD_GIT_COMMIT_SHA
    commit_sha = DDConfig.var(str, "git_commit_sha", default="")

    # DD_MAIN_PACKAGE
    main_package = DDConfig.var(str, "main_package", default="")

    # DD_TAGS
    tags = DDConfig.var(str, "tags", default="")


def _get_tags_from_env(config):
    # type: (GitMetadataConfig) -> typing.Tuple[str, str, str]
    """
    Get git metadata from environment variables.
    Returns tuple (repository_url, commit_sha, main_package)
    """
    repository_url = config.repository_url
    commit_sha = config.commit_sha
    main_package = config.main_package

    # Previously, the repository URL and commit SHA were derived from the DD_TAGS environment variable.
    # This approach was for backward compatibility before the introduction of DD_GIT_REPOSITORY_URL
    # and DD_GIT_COMMIT_SHA environment variables.
    tags = formats.parse_tags_str(config.tags)
    if not repository_url:
        repository_url = tags.get(REPOSITORY_URL, "")
    if not commit_sha:
        commit_sha = tags.get(COMMIT_SHA, "")
    filtered_git_url = _filter_sensitive_info(repository_url)
    if type(filtered_git_url) != str:
        return "", commit_sha, main_package
    return filtered_git_url, commit_sha, main_package


def _get_tags_from_package(main_package):
    # type: (str) -> typing.Tuple[str, str]
    """
    Extracts git metadata from python package's medatada field Project-URL:
    e.g: Project-URL: source_code_link, https://github.com/user/repo#gitcommitsha&someoptions
    Returns tuple (repository_url, commit_sha)
    """
    if not main_package:
        return "", ""
    dist = get_distribution(main_package)
    if not dist:
        return "", ""

    return (dist.repository_url, dist.commit_sha)


def get_git_tags():
    # type: () -> typing.Tuple[str, str, str]
    """
    Returns git metadata tags tuple (repository_url, commit_sha, main_package)
    """
    try:
        global _GITMETADATA_TAGS
        if _GITMETADATA_TAGS is not None:
            return _GITMETADATA_TAGS

        config = GitMetadataConfig()

        if config.enabled:
            repository_url, commit_sha, main_package = _get_tags_from_env(config)
            log.debug("git tags from env: %s %s %s", repository_url, commit_sha, main_package)
            if main_package and (not repository_url or not commit_sha):
                # trying to extract repo URL and/or commit sha from the main package
                pkg_repository_url, pkg_commit_sha = _get_tags_from_package(main_package)
                log.debug("git tags from package: %s %s", pkg_repository_url, pkg_commit_sha)
                if not repository_url:
                    repository_url = pkg_repository_url
                if not commit_sha:
                    commit_sha = pkg_commit_sha

            log.debug("git tags: %s %s", repository_url, commit_sha)
            _GITMETADATA_TAGS = repository_url, commit_sha, main_package
        else:
            log.debug("git tags disabled")
            _GITMETADATA_TAGS = ("", "", "")
        return _GITMETADATA_TAGS
    except Exception:
        log.debug("git tags failed", exc_info=True)
        return "", "", ""


def clean_tags(tags):
    # type: (typing.Dict[str, str]) -> typing.Dict[str, str]
    """
    Cleanup tags from git metadata
    """
    tags.pop(REPOSITORY_URL, None)
    tags.pop(COMMIT_SHA, None)
    tags.pop(MAIN_PACKAGE, None)

    return tags


def add_tags(tags):
    clean_tags(tags)

    repository_url, commit_sha, main_package = get_git_tags()

    if repository_url:
        tags[REPOSITORY_URL] = repository_url

    if commit_sha:
        tags[COMMIT_SHA] = commit_sha

    if main_package:
        tags[MAIN_PACKAGE] = main_package
