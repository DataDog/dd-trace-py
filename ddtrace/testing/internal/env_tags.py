import json
import logging
import os
import typing as t

from ddtrace.internal.settings import env
from ddtrace.testing.internal import ci
from ddtrace.testing.internal import git
from ddtrace.testing.internal.ci import CITag
from ddtrace.testing.internal.constants import DD_TEST_OPTIMIZATION_ENV_DATA_FILE
from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.git import get_pr_base_commit_sha
from ddtrace.testing.internal.git import get_workspace_path
from ddtrace.testing.internal.offline_mode import get_offline_mode
from ddtrace.testing.internal.offline_mode import resolve_rlocation
from ddtrace.testing.internal.telemetry import TelemetryAPI
from ddtrace.testing.internal.utils import _filter_sensitive_info


log = logging.getLogger(__name__)


_TagDict = dict[str, t.Optional[str]]

# Provider name constants used in commit SHA consistency telemetry
_PROVIDER_USER_SUPPLIED = "user_supplied"
_PROVIDER_CI = "ci_provider"
_PROVIDER_GIT_CLIENT = "git_client"


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


def _read_env_data_file() -> dict[str, str]:
    """Read CI/Git tags from the environmental data file if available.

    The Bazel rule provides pre-computed CI and Git context via
    ``DD_TEST_OPTIMIZATION_ENV_DATA_FILE``.  This replaces local Git CLI
    enrichment in payload-files mode.
    """

    path = env.get(DD_TEST_OPTIMIZATION_ENV_DATA_FILE)
    if not path:
        return {}
    path = resolve_rlocation(path)
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    except (OSError, json.JSONDecodeError, ValueError) as e:
        log.warning("Error reading env data file %s: %s", path, e)
    return {}


def _check_commit_sha_consistency(
    user_supplied_tags: _TagDict,
    ci_provider_tags: _TagDict,
    git_client_tags: _TagDict,
) -> None:
    """Compare git.commit.sha and git.repository_url values across providers and emit telemetry.

    Checks all provider pairs explicitly, matching the cross-language reference implementation:
    - Commit SHA: (ci_provider, git_client), (user_supplied, git_client), (user_supplied, ci_provider)
    - Repository URL: same three pairs

    Each check emits git.commit_sha_discrepancy when both sides are non-empty and differ.
    Always emits exactly one git.commit_sha_match metric.
    """
    if TelemetryAPI._instance is None:
        return

    u = user_supplied_tags.get
    c = ci_provider_tags.get
    g = git_client_tags.get

    checks = [
        # Commit SHA checks
        (c(GitTag.COMMIT_SHA), g(GitTag.COMMIT_SHA), "commit_discrepancy", _PROVIDER_CI, _PROVIDER_GIT_CLIENT),
        (
            u(GitTag.COMMIT_SHA),
            g(GitTag.COMMIT_SHA),
            "commit_discrepancy",
            _PROVIDER_USER_SUPPLIED,
            _PROVIDER_GIT_CLIENT,
        ),
        (u(GitTag.COMMIT_SHA), c(GitTag.COMMIT_SHA), "commit_discrepancy", _PROVIDER_USER_SUPPLIED, _PROVIDER_CI),
        # Repository URL checks
        (
            c(GitTag.REPOSITORY_URL),
            g(GitTag.REPOSITORY_URL),
            "repository_discrepancy",
            _PROVIDER_CI,
            _PROVIDER_GIT_CLIENT,
        ),
        (
            u(GitTag.REPOSITORY_URL),
            g(GitTag.REPOSITORY_URL),
            "repository_discrepancy",
            _PROVIDER_USER_SUPPLIED,
            _PROVIDER_GIT_CLIENT,
        ),
        (
            u(GitTag.REPOSITORY_URL),
            c(GitTag.REPOSITORY_URL),
            "repository_discrepancy",
            _PROVIDER_USER_SUPPLIED,
            _PROVIDER_CI,
        ),
    ]

    has_mismatch = False
    for left, right, discrepancy_type, expected_provider, discrepant_provider in checks:
        if left and right and left != right:
            has_mismatch = True
            TelemetryAPI.get().record_commit_sha_discrepancy(expected_provider, discrepant_provider, discrepancy_type)

    TelemetryAPI.get().record_commit_sha_match(not has_mismatch)


def get_env_tags() -> dict[str, str]:
    # NOTE: In payload-files mode (Bazel sandbox output), CI/Git/OS/runtime tags
    # must NOT be populated from the local environment or git CLI. Instead, the
    # Bazel rule provides pre-computed context via DD_TEST_OPTIMIZATION_ENV_DATA_FILE.

    offline = get_offline_mode()
    if offline.payload_files_enabled:
        log.debug("Payload-files mode active: reading tags from env data file instead of local git")
        env_data_tags = _read_env_data_file()
        # Bazel provider fallback: if no CI provider was detected, tag as "bazel"
        if CITag.PROVIDER_NAME not in env_data_tags:
            env_data_tags[CITag.PROVIDER_NAME] = "bazel"
        return env_data_tags

    tags: _TagDict = {}

    local_git_tags = git.get_git_tags_from_git_command()
    ci_provider_tags = ci.get_ci_tags(os.environ)
    user_supplied_tags = git.get_git_tags_from_dd_variables(os.environ)

    merge_tags(tags, local_git_tags, ci_provider_tags, user_supplied_tags, get_custom_dd_tags(os.environ))

    _check_commit_sha_consistency(user_supplied_tags, ci_provider_tags, local_git_tags)

    if head_sha := tags.get(GitTag.COMMIT_HEAD_SHA):
        merge_tags(tags, git.get_git_head_tags_from_git_command(head_sha))

    # For GitHub Actions: pull_request.base.sha is the HEAD of the base branch, not the merge base.
    # Compute the true base commit SHA via `git merge-base` when it hasn't already been set by the
    # CI provider (e.g. GitLab sets CI_MERGE_REQUEST_DIFF_BASE_SHA directly).
    if not tags.get(GitTag.PULL_REQUEST_BASE_BRANCH_SHA):
        base_branch_head_sha = tags.get(GitTag.PULL_REQUEST_BASE_BRANCH_HEAD_SHA)
        pr_head_sha = tags.get(GitTag.COMMIT_HEAD_SHA)
        if base_branch_head_sha and pr_head_sha:
            merge_base_sha = get_pr_base_commit_sha(base_branch_head_sha, pr_head_sha)
            if merge_base_sha:
                tags[GitTag.PULL_REQUEST_BASE_BRANCH_SHA] = merge_base_sha

    normalize_git_tags(tags)

    if workspace_path := tags.get(CITag.WORKSPACE_PATH):
        # DEV: expanduser() requires HOME to be correctly set, so there is no point in accepting the environment as a
        # parameter in this function, the variables have to be in os.environ.
        tags[CITag.WORKSPACE_PATH] = os.path.expanduser(workspace_path)
    else:
        tags[CITag.WORKSPACE_PATH] = str(get_workspace_path())

    # Allow JOB_ID environment variable to override job ID from any provider
    if job_id := env.get("JOB_ID"):
        tags[CITag.JOB_ID] = job_id

    # Bazel provider fallback (manifest-only mode without payload-files)
    if offline.manifest_enabled and not tags.get(CITag.PROVIDER_NAME):
        tags[CITag.PROVIDER_NAME] = "bazel"

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

    tags[GitTag.PULL_REQUEST_BASE_BRANCH] = git.normalize_ref(tags.get(GitTag.PULL_REQUEST_BASE_BRANCH))
    tags[GitTag.REPOSITORY_URL] = _filter_sensitive_info(tags.get(GitTag.REPOSITORY_URL))


def parse_tags_str(tags_str: t.Optional[str]) -> dict[str, str]:
    """
    Parses a string containing key-value pairs and returns a dictionary.
    Key-value pairs are delimited by ':', and pairs are separated by whitespace, comma, OR BOTH.

    This implementation aligns with the way tags are parsed by the Agent and other Datadog SDKs

    :param tags_str: A string of the above form to parse tags from.
    :return: A dict containing the tags that were parsed.
    """
    tags: dict[str, str] = {}
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
