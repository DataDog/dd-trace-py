import os
import typing as t

from ddtrace.testing.internal import ci
from ddtrace.testing.internal import git
from ddtrace.testing.internal.ci import CITag
from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.git import get_workspace_path
from ddtrace.testing.internal.utils import _filter_sensitive_info


_TagDict = t.Dict[str, t.Optional[str]]


def merge_tags(target: _TagDict, *tag_dicts: _TagDict) -> None:
    """
    Overwrite tags in the `target` dictionary with tags from the `tag_dicts`.

    Tags in `tag_dicts` that have a None or empty value are ignored. If the same tag appears in multiple `tag_dicts`,
    the last non-empty occurrence wins.
    """
    for tag_dict in tag_dicts:
        for k, v in tag_dict.items():
            if v:  # not None or empty string
                target[k] = v


def get_env_tags() -> t.Dict[str, str]:
    tags: _TagDict = {}

    merge_tags(
        tags,
        git.get_git_tags_from_git_command(),
        ci.get_ci_tags(os.environ),
        git.get_git_tags_from_dd_variables(os.environ),
        get_custom_dd_tags(os.environ),
    )

    if head_sha := tags.get(GitTag.COMMIT_HEAD_SHA):
        merge_tags(tags, git.get_git_head_tags_from_git_command(head_sha))

    normalize_git_tags(tags)

    if workspace_path := tags.get(CITag.WORKSPACE_PATH):
        # DEV: expanduser() requires HOME to be correctly set, so there is no point in accepting the environment as a
        # parameter in this function, the variables have to be in os.environ.
        tags[CITag.WORKSPACE_PATH] = os.path.expanduser(workspace_path)
    else:
        tags[CITag.WORKSPACE_PATH] = str(get_workspace_path())

    return {k: v for k, v in tags.items() if v}


def normalize_git_tags(tags: _TagDict) -> None:
    # if git.BRANCH is a tag, we associate its value to TAG instead of BRANCH
    branch = tags.get(GitTag.BRANCH)
    tag = tags.get(GitTag.TAG)

    if branch and git.is_ref_a_tag(branch):
        if tag:
            tags[GitTag.TAG] = git.normalize_ref(tag)
        else:
            tags[GitTag.TAG] = git.normalize_ref(branch)
        del tags[GitTag.BRANCH]
    else:
        tags[GitTag.BRANCH] = git.normalize_ref(branch)
        tags[GitTag.TAG] = git.normalize_ref(tag)

    tags[GitTag.REPOSITORY_URL] = _filter_sensitive_info(tags.get(GitTag.REPOSITORY_URL))


def parse_tags_str(tags_str: t.Optional[str]) -> t.Dict[str, str]:
    """
    Parses a string containing key-value pairs and returns a dictionary.
    Key-value pairs are delimited by ':', and pairs are separated by whitespace, comma, OR BOTH.

    This implementation aligns with the way tags are parsed by the Agent and other Datadog SDKs

    :param tags_str: A string of the above form to parse tags from.
    :return: A dict containing the tags that were parsed.
    """
    tags: t.Dict[str, str] = {}
    if not tags_str:
        return tags

    # falling back to comma as separator
    separator = "," if "," in tags_str else " "

    for tag in tags_str.split(separator):
        tag = tag.strip()
        if not tag:
            # skip empty tags
            continue
        elif ":" in tag:
            # if tag contains a colon, split on the first colon
            key, val = tag.split(":", 1)
        else:
            # if tag does not contain a colon, use the whole string as the key
            key, val = tag, ""
        key, val = key.strip(), val.strip()
        if key:
            # only add the tag if the key is not empty
            tags[key] = val
    return tags


def get_custom_dd_tags(env: t.MutableMapping[str, str]) -> _TagDict:
    tags: _TagDict = {}
    tags.update(parse_tags_str(env.get("DD_TAGS")))
    tags.update(parse_tags_str(env.get("_CI_DD_TAGS")))
    return tags
