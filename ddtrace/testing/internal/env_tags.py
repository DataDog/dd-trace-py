import os
import typing as t

from ddtestpy.internal import ci
from ddtestpy.internal import git
from ddtestpy.internal.ci import CITag
from ddtestpy.internal.git import GitTag
from ddtestpy.internal.git import get_workspace_path
from ddtestpy.internal.utils import _filter_sensitive_info


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
