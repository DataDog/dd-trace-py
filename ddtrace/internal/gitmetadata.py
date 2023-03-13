import os
import typing

from envier import Env

from ddtrace.internal.utils import formats


_GITMETADATA_TAGS = None  # type: typing.Optional[typing.Dict[str, str]]

TAG_REPOSITORY_URL = "git.repository_url"
TAG_COMMIT_SHA = "git.commit.sha"

TRACE_TAG_REPOSITORY_URL = "_dd.git.repository_url"
TRACE_TAG_COMMIT_SHA = "_dd.git.commit.sha"


class GitMetadataConfig(Env):
    __prefix__ = "dd"

    # DD_TRACE_GIT_METADATA_ENABLED
    enabled = Env.var(bool, "trace_git_metadata_enabled", default=True)

    # DD_GIT_REPOSITORY_URL
    repository_url = Env.var(str, "git.repository_url", default="")

    # DD_GIT_COMMIT_SHA
    commit_sha = Env.var(str, "git.commit.sha", default="")

    # DD_MAIN_PACKAGE
    main_package = Env.var(str, "main_package", default="")

    # DD_TAGS
    tags = Env.var(str, "tags", default="")


def __get_tags_from_env(config):
    # type: () -> typing.Optional[typing.Dict[str, str]]
    """
    Get git metadata from environment variables
    """
    repository_url = config.repository_url
    commit_sha = config.commit_sha

    if not (repository_url and commit_sha):
        tags = formats.parse_tags_str(config.tags)
        repository_url = tags.get(TAG_REPOSITORY_URL)
        commit_sha = tags.get(TAG_COMMIT_SHA)

    if not (repository_url and commit_sha):
        return None

    return {TRACE_TAG_REPOSITORY_URL: repository_url, TRACE_TAG_COMMIT_SHA: commit_sha}


def __get_tags_from_package(config):
    # type: () -> typing.Dict[str, str]
    """
    Extracts git metadata from python package's medatada field Project-URL:
    e.g: Project-URL: source_code_link, https://github.com/user/repo#gitcommitsha&someoptions
    """
    if config.main_package == "":
        return {}
    try:
        try:
            import importlib.metadata as importlib_metadata
        except ImportError:
            import importlib_metadata  # type: ignore[no-redef]

        source_code_link = ""
        for val in importlib_metadata.metadata(config.main_package).get_all("Project-URL"):
            capt_val = val.split(", ")
            if capt_val[0] == "source_code_link":
                source_code_link = capt_val[1].strip()
                break

        if source_code_link != "" and "#" in source_code_link:
            repository_url, commit_sha = source_code_link.split("#")
            commit_sha = commit_sha.split("&")[0]
            return {TRACE_TAG_REPOSITORY_URL: repository_url, TRACE_TAG_COMMIT_SHA: commit_sha}
        return {}
    except importlib_metadata.PackageNotFoundError:
        return {}


def get_tracer_tags():
    # type: () -> typing.Dict[str, str]
    """
    Returns git metadata tags for tracer
    """
    global _GITMETADATA_TAGS
    if _GITMETADATA_TAGS is not None:
        return _GITMETADATA_TAGS

    config = GitMetadataConfig()

    if config.enabled:
        tags = __get_tags_from_env(config)
        if tags is None:
            tags = __get_tags_from_package(config)
        _GITMETADATA_TAGS = tags
    else:
        _GITMETADATA_TAGS = {}
    return _GITMETADATA_TAGS


def clean_tags(tags):
    # type: (typing.Dict[str, str]) -> typing.Dict[str, str]
    """
    Cleanup tags from git metadata
    """
    tags.pop(TAG_REPOSITORY_URL, None)
    tags.pop(TAG_COMMIT_SHA, None)

    return tags


def update_profiler_tags(tags):
    # type: (typing.Dict[str, str]) -> typing.Dict[str, str]
    """
    Update profiler tags with git metadata
    """
    tracer_tags = get_tracer_tags()
    clean_tags(tags)
    if TRACE_TAG_REPOSITORY_URL in tracer_tags and TRACE_TAG_COMMIT_SHA in tracer_tags:
        tags[TAG_REPOSITORY_URL] = tracer_tags[TRACE_TAG_REPOSITORY_URL]
        tags[TAG_COMMIT_SHA] = tracer_tags[TRACE_TAG_COMMIT_SHA]

    return tags
