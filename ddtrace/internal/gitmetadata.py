import os
import typing

from ddtrace.internal.utils import formats


__GITMETADATA_TAGS = None  # type: typing.Optional[typing.Dict[str, str]]

ENV_ENABLED_FLAG = "DD_TRACE_GIT_METADATA_ENABLED"
ENV_REPOSITORY_URL = "DD_GIT_REPOSITORY_URL"
ENV_COMMIT_SHA = "DD_GIT_COMMIT_SHA"
ENV_MAIN_PACKAGE = "DD_MAIN_PACKAGE"
ENV_GLOBAL_TAGS = "DD_TAGS"

TAG_REPOSITORY_URL = "git.repository_url"
TAG_COMMIT_SHA = "git.commit.sha"

TRACE_TAG_REPOSITORY_URL = "_dd.git.repository_url"
TRACE_TAG_COMMIT_SHA = "_dd.git.commit.sha"


def __get_tags_from_env():
    # type: () -> typing.Optional[typing.Dict[str, str]]
    """
    Get git metadata from environment variables
    """
    repository_url = os.getenv(ENV_REPOSITORY_URL)
    commit_sha = os.getenv(ENV_COMMIT_SHA)

    if not (repository_url and commit_sha):
        tags = formats.parse_tags_str(os.getenv(ENV_GLOBAL_TAGS, ""))
        repository_url = tags.get(TAG_REPOSITORY_URL)
        commit_sha = tags.get(TAG_COMMIT_SHA)

    if not (repository_url and commit_sha):
        return None

    return {TRACE_TAG_REPOSITORY_URL: repository_url, TRACE_TAG_COMMIT_SHA: commit_sha}


def __get_tags_from_package():
    # type: () -> typing.Dict[str, str]
    """
    Extracts git metadata from python package's medatada field Project-URL:
    e.g: Project-URL: source_code_link, https://github.com/user/repo#gitcommitsha&someoptions
    """
    package = os.getenv(ENV_MAIN_PACKAGE, "")
    if package == "":
        return {}
    try:
        try:
            import importlib.metadata as importlib_metadata
        except ImportError:
            import importlib_metadata  # type: ignore[no-redef]

        source_code_link = ""
        for val in importlib_metadata.metadata(package).get_all("Project-URL"):
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
    global __GITMETADATA_TAGS
    if __GITMETADATA_TAGS is not None:
        return __GITMETADATA_TAGS

    if formats.asbool(os.getenv(ENV_ENABLED_FLAG, "True")):
        tags = __get_tags_from_env()
        if tags is None:
            tags = __get_tags_from_package()
        __GITMETADATA_TAGS = tags
    else:
        __GITMETADATA_TAGS = {}
    return __GITMETADATA_TAGS


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
    if TRACE_TAG_REPOSITORY_URL in tracer_tags and TRACE_TAG_COMMIT_SHA in tracer_tags:
        tags[TAG_REPOSITORY_URL] = tracer_tags[TRACE_TAG_REPOSITORY_URL]
        tags[TAG_COMMIT_SHA] = tracer_tags[TRACE_TAG_COMMIT_SHA]

    return tags
