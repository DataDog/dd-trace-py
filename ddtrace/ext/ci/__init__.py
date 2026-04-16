"""
Tags for common CI attributes
"""

import json
import logging
import os
import platform
import re
from typing import MutableMapping  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace.ext import git
from ddtrace.ext.ci import github_actions
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env


# CI app dd_origin tag
CI_APP_TEST_ORIGIN = "ciapp-test"

# Stage Name
STAGE_NAME = "ci.stage.name"

# Job ID
JOB_ID = "ci.job.id"

# Job Name
JOB_NAME = "ci.job.name"

# Job URL
JOB_URL = "ci.job.url"

# Pipeline ID
PIPELINE_ID = "ci.pipeline.id"

# Pipeline Name
PIPELINE_NAME = "ci.pipeline.name"

# Pipeline Number
PIPELINE_NUMBER = "ci.pipeline.number"

# Pipeline URL
PIPELINE_URL = "ci.pipeline.url"

# Provider
PROVIDER_NAME = "ci.provider.name"

# CI Node Name
NODE_NAME = "ci.node.name"

# CI Node Labels
NODE_LABELS = "ci.node.labels"

# Workspace Path
WORKSPACE_PATH = "ci.workspace_path"

# Architecture
OS_ARCHITECTURE = "os.architecture"

# Platform
OS_PLATFORM = "os.platform"

# Version
OS_VERSION = "os.version"

# Runtime Name
RUNTIME_NAME = "runtime.name"

# Runtime Version
RUNTIME_VERSION = "runtime.version"

# Version of the ddtrace library
LIBRARY_VERSION = "library_version"

# CI Visibility env vars used for pipeline correlation ID
_CI_ENV_VARS = "_dd.ci.env_vars"

_RE_URL = re.compile(r"(https?://|ssh://)[^/]*@")


log = get_logger(__name__)


def _filter_sensitive_info(url: Optional[str]) -> Optional[str]:
    return _RE_URL.sub("\\1", url) if url is not None else None


def _get_runtime_and_os_metadata():
    """Extract configuration facet tags for OS and Python runtime."""
    return {
        OS_ARCHITECTURE: platform.machine(),
        OS_PLATFORM: platform.system(),
        OS_VERSION: platform.release(),
        RUNTIME_NAME: platform.python_implementation(),
        RUNTIME_VERSION: platform.python_version(),
    }


def tags(environ: Optional[MutableMapping[str, str]] = None, cwd: Optional[str] = None) -> dict[str, str]:
    """Extract and set tags from provider environ, as well as git metadata."""
    environ = env if environ is None else environ
    tags: dict[str, Optional[str]] = {}
    for key, extract in PROVIDERS:
        if key in environ:
            tags = extract(environ)
            break

    git_info = git.extract_git_metadata(cwd=cwd)

    # Whenever the HEAD commit SHA is present in the tags that come from the CI provider, we assume that
    # the CI provider added a commit on top of the user's HEAD commit (e.g., GitHub Actions add a merge
    # commit when triggered by a pull request). In that case, we extract the metadata for that commit specifically
    # and add it to the tags.
    head_commit_sha = tags.get(git.COMMIT_HEAD_SHA)
    if head_commit_sha:
        git_head_info = git.extract_git_head_metadata(head_commit_sha=head_commit_sha, cwd=cwd)
        git_info.update(git_head_info)

    try:
        git_info[WORKSPACE_PATH] = git.extract_workspace_path(cwd=cwd)
    except git.GitNotFoundError:
        log.error("Git executable not found, cannot extract git metadata.")
    except ValueError as e:
        debug_mode = log.isEnabledFor(logging.DEBUG)
        stderr = str(e)
        log.error("Error extracting git metadata: %s", stderr, exc_info=debug_mode)

    # Tags collected from CI provider take precedence over extracted git metadata, but any CI provider value
    # is None or "" should be overwritten.
    tags.update({k: v for k, v in git_info.items() if not tags.get(k)})

    user_specified_git_info = git.extract_user_git_metadata(environ)

    # Tags provided by the user take precedence over everything
    tags.update({k: v for k, v in user_specified_git_info.items() if v})

    # Allow JOB_ID environment variable to override job ID from any provider
    if environ.get("JOB_ID"):
        tags[JOB_ID] = environ.get("JOB_ID")

    # if git.BRANCH is a tag, we associate its value to TAG instead of BRANCH
    if git.is_ref_a_tag(tags.get(git.BRANCH)):
        if not tags.get(git.TAG):
            tags[git.TAG] = git.normalize_ref(tags.get(git.BRANCH))
        else:
            tags[git.TAG] = git.normalize_ref(tags.get(git.TAG))
        del tags[git.BRANCH]
    else:
        tags[git.BRANCH] = git.normalize_ref(tags.get(git.BRANCH))
        tags[git.TAG] = git.normalize_ref(tags.get(git.TAG))

    tags[git.PULL_REQUEST_BASE_BRANCH] = git.normalize_ref(tags.get(git.PULL_REQUEST_BASE_BRANCH))

    tags[git.REPOSITORY_URL] = _filter_sensitive_info(tags.get(git.REPOSITORY_URL))

    workspace_path = tags.get(WORKSPACE_PATH)
    if workspace_path:
        tags[WORKSPACE_PATH] = os.path.expanduser(workspace_path)

    tags.update(_get_runtime_and_os_metadata())

    return {k: v for k, v in tags.items() if v is not None}


def extract_appveyor(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Appveyor environ."""
    url = "https://ci.appveyor.com/project/{0}/builds/{1}".format(
        environ.get("APPVEYOR_REPO_NAME"), environ.get("APPVEYOR_BUILD_ID")
    )
    is_pull_request = bool(environ.get("APPVEYOR_PULL_REQUEST_HEAD_REPO_BRANCH"))
    if environ.get("APPVEYOR_REPO_PROVIDER") == "github":
        repository: Optional[str] = "https://github.com/{0}.git".format(environ.get("APPVEYOR_REPO_NAME"))
        commit: Optional[str] = environ.get("APPVEYOR_REPO_COMMIT")
        branch: Optional[str] = environ.get(
            "APPVEYOR_PULL_REQUEST_HEAD_REPO_BRANCH", environ.get("APPVEYOR_REPO_BRANCH")
        )
        tag: Optional[str] = environ.get("APPVEYOR_REPO_TAG_NAME")
    else:
        repository = commit = branch = tag = None

    commit_message = environ.get("APPVEYOR_REPO_COMMIT_MESSAGE")
    if commit_message:
        extended = environ.get("APPVEYOR_REPO_COMMIT_MESSAGE_EXTENDED")
        if extended:
            commit_message += "\n" + extended

    return {
        PROVIDER_NAME: "appveyor",
        git.REPOSITORY_URL: repository,
        git.COMMIT_SHA: commit,
        git.COMMIT_HEAD_SHA: environ.get("APPVEYOR_PULL_REQUEST_HEAD_COMMIT"),
        WORKSPACE_PATH: environ.get("APPVEYOR_BUILD_FOLDER"),
        PIPELINE_ID: environ.get("APPVEYOR_BUILD_ID"),
        PIPELINE_NAME: environ.get("APPVEYOR_REPO_NAME"),
        PIPELINE_NUMBER: environ.get("APPVEYOR_BUILD_NUMBER"),
        PIPELINE_URL: url,
        JOB_URL: url,
        git.BRANCH: branch,
        git.PULL_REQUEST_BASE_BRANCH: environ.get("APPVEYOR_REPO_BRANCH") if is_pull_request else None,
        git.TAG: tag,
        git.COMMIT_MESSAGE: commit_message,
        git.COMMIT_AUTHOR_NAME: environ.get("APPVEYOR_REPO_COMMIT_AUTHOR"),
        git.COMMIT_AUTHOR_EMAIL: environ.get("APPVEYOR_REPO_COMMIT_AUTHOR_EMAIL"),
        git.PULL_REQUEST_NUMBER: environ.get("APPVEYOR_PULL_REQUEST_NUMBER"),
    }


def extract_azure_pipelines(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Azure pipelines environ."""
    if (
        environ.get("SYSTEM_TEAMFOUNDATIONSERVERURI")
        and environ.get("SYSTEM_TEAMPROJECTID")
        and environ.get("BUILD_BUILDID")
    ):
        base_url = "{0}{1}/_build/results?buildId={2}".format(
            environ.get("SYSTEM_TEAMFOUNDATIONSERVERURI"),
            environ.get("SYSTEM_TEAMPROJECTID"),
            environ.get("BUILD_BUILDID"),
        )
        pipeline_url: Optional[str] = base_url
        job_url: Optional[str] = base_url + "&view=logs&j={0}&t={1}".format(
            environ.get("SYSTEM_JOBID"), environ.get("SYSTEM_TASKINSTANCEID")
        )
    else:
        pipeline_url = job_url = None

    return {
        PROVIDER_NAME: "azurepipelines",
        WORKSPACE_PATH: environ.get("BUILD_SOURCESDIRECTORY"),
        PIPELINE_ID: environ.get("BUILD_BUILDID"),
        PIPELINE_NAME: environ.get("BUILD_DEFINITIONNAME"),
        PIPELINE_NUMBER: environ.get("BUILD_BUILDID"),
        PIPELINE_URL: pipeline_url,
        JOB_URL: job_url,
        git.REPOSITORY_URL: environ.get("SYSTEM_PULLREQUEST_SOURCEREPOSITORYURI")
        or environ.get("BUILD_REPOSITORY_URI"),
        git.COMMIT_SHA: environ.get("SYSTEM_PULLREQUEST_SOURCECOMMITID") or environ.get("BUILD_SOURCEVERSION"),
        git.BRANCH: environ.get("SYSTEM_PULLREQUEST_SOURCEBRANCH")
        or environ.get("BUILD_SOURCEBRANCH")
        or environ.get("BUILD_SOURCEBRANCHNAME"),
        git.PULL_REQUEST_BASE_BRANCH: environ.get("SYSTEM_PULLREQUEST_TARGETBRANCH"),
        git.COMMIT_MESSAGE: environ.get("BUILD_SOURCEVERSIONMESSAGE"),
        git.COMMIT_AUTHOR_NAME: environ.get("BUILD_REQUESTEDFORID"),
        git.COMMIT_AUTHOR_EMAIL: environ.get("BUILD_REQUESTEDFOREMAIL"),
        STAGE_NAME: environ.get("SYSTEM_STAGEDISPLAYNAME"),
        JOB_ID: environ.get("SYSTEM_JOBID"),
        JOB_NAME: environ.get("SYSTEM_JOBDISPLAYNAME"),
        _CI_ENV_VARS: json.dumps(
            {
                "SYSTEM_TEAMPROJECTID": environ.get("SYSTEM_TEAMPROJECTID"),
                "BUILD_BUILDID": environ.get("BUILD_BUILDID"),
                "SYSTEM_JOBID": environ.get("SYSTEM_JOBID"),
            },
            separators=(",", ":"),
        ),
        git.PULL_REQUEST_NUMBER: environ.get("SYSTEM_PULLREQUEST_PULLREQUESTNUMBER"),
    }


def extract_bitbucket(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Bitbucket environ."""
    url = "https://bitbucket.org/{0}/addon/pipelines/home#!/results/{1}".format(
        environ.get("BITBUCKET_REPO_FULL_NAME"), environ.get("BITBUCKET_BUILD_NUMBER")
    )
    return {
        git.BRANCH: environ.get("BITBUCKET_BRANCH"),
        git.COMMIT_SHA: environ.get("BITBUCKET_COMMIT"),
        git.PULL_REQUEST_BASE_BRANCH: environ.get("BITBUCKET_PR_DESTINATION_BRANCH"),
        git.REPOSITORY_URL: environ.get("BITBUCKET_GIT_SSH_ORIGIN") or environ.get("BITBUCKET_GIT_HTTP_ORIGIN"),
        git.TAG: environ.get("BITBUCKET_TAG"),
        JOB_URL: url,
        PIPELINE_ID: environ.get("BITBUCKET_PIPELINE_UUID", "").strip("{}}") or None,  # noqa: B005
        PIPELINE_NAME: environ.get("BITBUCKET_REPO_FULL_NAME"),
        PIPELINE_NUMBER: environ.get("BITBUCKET_BUILD_NUMBER"),
        PIPELINE_URL: url,
        PROVIDER_NAME: "bitbucket",
        WORKSPACE_PATH: environ.get("BITBUCKET_CLONE_DIR"),
        git.PULL_REQUEST_NUMBER: environ.get("BITBUCKET_PR_ID"),
    }


def extract_buildkite(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Buildkite environ."""
    # Get all keys which start with BUILDKITE_AGENT_META_DATA_x
    pull_request_number = environ.get("BUILDKITE_PULL_REQUEST")
    is_pull_request = pull_request_number not in (None, "", "false")
    node_label_list: list[str] = []
    buildkite_agent_meta_data_prefix = "BUILDKITE_AGENT_META_DATA_"
    for env_variable in environ:
        if env_variable.startswith(buildkite_agent_meta_data_prefix):
            key = env_variable.replace(buildkite_agent_meta_data_prefix, "").lower()
            value = environ.get(env_variable)
            node_label_list.append("{}:{}".format(key, value))
    return {
        git.BRANCH: environ.get("BUILDKITE_BRANCH"),
        git.COMMIT_SHA: environ.get("BUILDKITE_COMMIT"),
        git.PULL_REQUEST_BASE_BRANCH: environ.get("BUILDKITE_PULL_REQUEST_BASE_BRANCH") if is_pull_request else None,
        git.REPOSITORY_URL: environ.get("BUILDKITE_REPO"),
        git.TAG: environ.get("BUILDKITE_TAG"),
        PIPELINE_ID: environ.get("BUILDKITE_BUILD_ID"),
        PIPELINE_NAME: environ.get("BUILDKITE_PIPELINE_SLUG"),
        PIPELINE_NUMBER: environ.get("BUILDKITE_BUILD_NUMBER"),
        PIPELINE_URL: environ.get("BUILDKITE_BUILD_URL"),
        JOB_ID: environ.get("BUILDKITE_JOB_ID"),
        JOB_URL: "{0}#{1}".format(environ.get("BUILDKITE_BUILD_URL"), environ.get("BUILDKITE_JOB_ID")),
        PROVIDER_NAME: "buildkite",
        WORKSPACE_PATH: environ.get("BUILDKITE_BUILD_CHECKOUT_PATH"),
        git.COMMIT_MESSAGE: environ.get("BUILDKITE_MESSAGE"),
        git.COMMIT_AUTHOR_NAME: environ.get("BUILDKITE_BUILD_AUTHOR"),
        git.COMMIT_AUTHOR_EMAIL: environ.get("BUILDKITE_BUILD_AUTHOR_EMAIL"),
        git.COMMIT_COMMITTER_NAME: environ.get("BUILDKITE_BUILD_CREATOR"),
        git.COMMIT_COMMITTER_EMAIL: environ.get("BUILDKITE_BUILD_CREATOR_EMAIL"),
        _CI_ENV_VARS: json.dumps(
            {
                "BUILDKITE_BUILD_ID": environ.get("BUILDKITE_BUILD_ID"),
                "BUILDKITE_JOB_ID": environ.get("BUILDKITE_JOB_ID"),
            },
            separators=(",", ":"),
        ),
        NODE_LABELS: json.dumps(node_label_list, separators=(",", ":")),
        NODE_NAME: environ.get("BUILDKITE_AGENT_ID"),
        git.PULL_REQUEST_NUMBER: pull_request_number if is_pull_request else None,
    }


def extract_circle_ci(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from CircleCI environ."""
    return {
        git.BRANCH: environ.get("CIRCLE_BRANCH"),
        git.COMMIT_SHA: environ.get("CIRCLE_SHA1"),
        git.REPOSITORY_URL: environ.get("CIRCLE_REPOSITORY_URL"),
        git.TAG: environ.get("CIRCLE_TAG"),
        JOB_ID: environ.get("CIRCLE_BUILD_NUM"),
        PIPELINE_ID: environ.get("CIRCLE_WORKFLOW_ID"),
        PIPELINE_NAME: environ.get("CIRCLE_PROJECT_REPONAME"),
        PIPELINE_NUMBER: environ.get("CIRCLE_BUILD_NUM"),
        PIPELINE_URL: "https://app.circleci.com/pipelines/workflows/{0}".format(environ.get("CIRCLE_WORKFLOW_ID")),
        JOB_URL: environ.get("CIRCLE_BUILD_URL"),
        JOB_NAME: environ.get("CIRCLE_JOB"),
        PROVIDER_NAME: "circleci",
        WORKSPACE_PATH: environ.get("CIRCLE_WORKING_DIRECTORY"),
        git.PULL_REQUEST_NUMBER: environ.get("CIRCLE_PR_NUMBER"),
        _CI_ENV_VARS: json.dumps(
            {
                "CIRCLE_WORKFLOW_ID": environ.get("CIRCLE_WORKFLOW_ID"),
                "CIRCLE_BUILD_NUM": environ.get("CIRCLE_BUILD_NUM"),
            },
            separators=(",", ":"),
        ),
    }


def extract_codefresh(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Codefresh environ."""
    build_id = environ.get("CF_BUILD_ID")
    return {
        git.BRANCH: environ.get("CF_BRANCH"),
        git.PULL_REQUEST_BASE_BRANCH: environ.get("CF_PULL_REQUEST_TARGET"),
        PIPELINE_ID: build_id,
        PIPELINE_NAME: environ.get("CF_PIPELINE_NAME"),
        PIPELINE_URL: environ.get("CF_BUILD_URL"),
        JOB_NAME: environ.get("CF_STEP_NAME"),
        PROVIDER_NAME: "codefresh",
        _CI_ENV_VARS: json.dumps(
            {"CF_BUILD_ID": build_id},
            separators=(",", ":"),
        ),
        git.PULL_REQUEST_NUMBER: environ.get("CF_PULL_REQUEST_NUMBER"),
    }


def extract_github_actions(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Github Actions environment."""
    return github_actions.extract_github_actions(environ)


def extract_gitlab(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Gitlab environ."""
    author = environ.get("CI_COMMIT_AUTHOR")
    author_name: Optional[str] = None
    author_email: Optional[str] = None
    if author:
        # Extract name and email from `author` which is in the form "name <email>"
        author_name, author_email = author.strip("> ").split(" <")
    commit_timestamp = environ.get("CI_COMMIT_TIMESTAMP")
    return {
        git.BRANCH: environ.get("CI_COMMIT_REF_NAME"),
        git.COMMIT_SHA: environ.get("CI_COMMIT_SHA"),
        git.PULL_REQUEST_BASE_BRANCH: environ.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME"),
        git.REPOSITORY_URL: environ.get("CI_REPOSITORY_URL"),
        git.TAG: environ.get("CI_COMMIT_TAG"),
        STAGE_NAME: environ.get("CI_JOB_STAGE"),
        JOB_ID: environ.get("CI_JOB_ID"),
        JOB_NAME: environ.get("CI_JOB_NAME"),
        JOB_URL: environ.get("CI_JOB_URL"),
        PIPELINE_ID: environ.get("CI_PIPELINE_ID"),
        PIPELINE_NAME: environ.get("CI_PROJECT_PATH"),
        PIPELINE_NUMBER: environ.get("CI_PIPELINE_IID"),
        PIPELINE_URL: environ.get("CI_PIPELINE_URL"),
        PROVIDER_NAME: "gitlab",
        WORKSPACE_PATH: environ.get("CI_PROJECT_DIR"),
        git.COMMIT_MESSAGE: environ.get("CI_COMMIT_MESSAGE"),
        git.COMMIT_AUTHOR_NAME: author_name,
        git.COMMIT_AUTHOR_EMAIL: author_email,
        git.COMMIT_AUTHOR_DATE: commit_timestamp,
        _CI_ENV_VARS: json.dumps(
            {
                "CI_PROJECT_URL": environ.get("CI_PROJECT_URL"),
                "CI_PIPELINE_ID": environ.get("CI_PIPELINE_ID"),
                "CI_JOB_ID": environ.get("CI_JOB_ID"),
            },
            separators=(",", ":"),
        ),
        NODE_LABELS: environ.get("CI_RUNNER_TAGS"),
        NODE_NAME: environ.get("CI_RUNNER_ID"),
        git.PULL_REQUEST_BASE_BRANCH_HEAD_SHA: environ.get("CI_MERGE_REQUEST_TARGET_BRANCH_SHA"),
        git.PULL_REQUEST_BASE_BRANCH_SHA: environ.get("CI_MERGE_REQUEST_DIFF_BASE_SHA"),
        git.COMMIT_HEAD_SHA: environ.get("CI_MERGE_REQUEST_SOURCE_BRANCH_SHA"),
        git.PULL_REQUEST_NUMBER: environ.get("CI_MERGE_REQUEST_IID"),
    }


def extract_jenkins(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Jenkins environ."""
    branch = environ.get("GIT_BRANCH", "")
    name = environ.get("JOB_NAME")
    if name and branch:
        name = re.sub("/{0}".format(git.normalize_ref(branch)), "", name)
    if name:
        name = "/".join((v for v in name.split("/") if v and "=" not in v))
    node_labels_list: list[str] = []
    node_labels_env: Optional[str] = environ.get("NODE_LABELS")
    if node_labels_env:
        node_labels_list = node_labels_env.split()
    return {
        git.BRANCH: environ.get("GIT_BRANCH"),
        git.COMMIT_SHA: environ.get("GIT_COMMIT"),
        git.PULL_REQUEST_BASE_BRANCH: environ.get("CHANGE_TARGET"),
        git.REPOSITORY_URL: environ.get("GIT_URL", environ.get("GIT_URL_1")),
        PIPELINE_ID: environ.get("BUILD_TAG"),
        PIPELINE_NAME: name,
        PIPELINE_NUMBER: environ.get("BUILD_NUMBER"),
        PIPELINE_URL: environ.get("BUILD_URL"),
        PROVIDER_NAME: "jenkins",
        WORKSPACE_PATH: environ.get("WORKSPACE"),
        _CI_ENV_VARS: json.dumps(
            {
                "DD_CUSTOM_TRACE_ID": environ.get("DD_CUSTOM_TRACE_ID"),
            },
            separators=(",", ":"),
        ),
        NODE_LABELS: json.dumps(node_labels_list, separators=(",", ":")),
        NODE_NAME: environ.get("NODE_NAME"),
        git.PULL_REQUEST_NUMBER: environ.get("CHANGE_ID"),
    }


def extract_teamcity(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Teamcity environ."""
    return {
        JOB_URL: environ.get("BUILD_URL"),
        JOB_NAME: environ.get("TEAMCITY_BUILDCONF_NAME"),
        PROVIDER_NAME: "teamcity",
        git.PULL_REQUEST_NUMBER: environ.get("TEAMCITY_PULLREQUEST_NUMBER"),
        git.PULL_REQUEST_BASE_BRANCH: environ.get("TEAMCITY_PULLREQUEST_TARGET_BRANCH"),
    }


def extract_travis(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Travis environ."""
    is_pull_request = environ.get("TRAVIS_EVENT_TYPE") == "pull_request"
    return {
        git.BRANCH: environ.get("TRAVIS_PULL_REQUEST_BRANCH") or environ.get("TRAVIS_BRANCH"),
        git.COMMIT_SHA: environ.get("TRAVIS_COMMIT"),
        git.COMMIT_HEAD_SHA: environ.get("TRAVIS_PULL_REQUEST_SHA") if is_pull_request else None,
        git.PULL_REQUEST_BASE_BRANCH: environ.get("TRAVIS_BRANCH") if is_pull_request else None,
        git.REPOSITORY_URL: "https://github.com/{0}.git".format(environ.get("TRAVIS_REPO_SLUG")),
        git.TAG: environ.get("TRAVIS_TAG"),
        JOB_URL: environ.get("TRAVIS_JOB_WEB_URL"),
        PIPELINE_ID: environ.get("TRAVIS_BUILD_ID"),
        PIPELINE_NAME: environ.get("TRAVIS_REPO_SLUG"),
        PIPELINE_NUMBER: environ.get("TRAVIS_BUILD_NUMBER"),
        PIPELINE_URL: environ.get("TRAVIS_BUILD_WEB_URL"),
        PROVIDER_NAME: "travisci",
        WORKSPACE_PATH: environ.get("TRAVIS_BUILD_DIR"),
        git.COMMIT_MESSAGE: environ.get("TRAVIS_COMMIT_MESSAGE"),
        git.PULL_REQUEST_NUMBER: environ.get("TRAVIS_PULL_REQUEST") if is_pull_request else None,
    }


def extract_bitrise(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Bitrise environ."""
    commit = environ.get("BITRISE_GIT_COMMIT") or environ.get("GIT_CLONE_COMMIT_HASH")
    branch = environ.get("BITRISEIO_PULL_REQUEST_HEAD_BRANCH") or environ.get("BITRISE_GIT_BRANCH")
    if environ.get("BITRISE_GIT_MESSAGE"):
        message: Optional[str] = environ.get("BITRISE_GIT_MESSAGE")
    elif environ.get("GIT_CLONE_COMMIT_MESSAGE_SUBJECT") or environ.get("GIT_CLONE_COMMIT_MESSAGE_BODY"):
        message = "{0}:\n{1}".format(
            environ.get("GIT_CLONE_COMMIT_MESSAGE_SUBJECT"), environ.get("GIT_CLONE_COMMIT_MESSAGE_BODY")
        )
    else:
        message = None

    return {
        PROVIDER_NAME: "bitrise",
        PIPELINE_ID: environ.get("BITRISE_BUILD_SLUG"),
        PIPELINE_NAME: environ.get("BITRISE_TRIGGERED_WORKFLOW_ID"),
        PIPELINE_NUMBER: environ.get("BITRISE_BUILD_NUMBER"),
        PIPELINE_URL: environ.get("BITRISE_BUILD_URL"),
        WORKSPACE_PATH: environ.get("BITRISE_SOURCE_DIR"),
        git.REPOSITORY_URL: environ.get("GIT_REPOSITORY_URL"),
        git.COMMIT_SHA: commit,
        git.BRANCH: branch,
        git.PULL_REQUEST_BASE_BRANCH: environ.get("BITRISEIO_GIT_BRANCH_DEST"),
        git.TAG: environ.get("BITRISE_GIT_TAG"),
        git.COMMIT_MESSAGE: message,
        git.COMMIT_AUTHOR_NAME: environ.get("GIT_CLONE_COMMIT_AUTHOR_NAME"),
        git.COMMIT_AUTHOR_EMAIL: environ.get("GIT_CLONE_COMMIT_AUTHOR_EMAIL"),
        git.COMMIT_COMMITTER_NAME: environ.get("GIT_CLONE_COMMIT_COMMITER_NAME"),
        git.COMMIT_COMMITTER_EMAIL: environ.get("GIT_CLONE_COMMIT_COMMITER_EMAIL")
        or environ.get("GIT_CLONE_COMMIT_COMMITER_NAME"),
        git.PULL_REQUEST_NUMBER: environ.get("BITRISE_PULL_REQUEST"),
    }


def extract_buddy(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Buddy environ."""
    return {
        PROVIDER_NAME: "buddy",
        PIPELINE_ID: "{0}/{1}".format(environ.get("BUDDY_PIPELINE_ID"), environ.get("BUDDY_EXECUTION_ID")),
        PIPELINE_NAME: environ.get("BUDDY_PIPELINE_NAME"),
        PIPELINE_NUMBER: environ.get("BUDDY_EXECUTION_ID"),
        PIPELINE_URL: environ.get("BUDDY_EXECUTION_URL"),
        git.REPOSITORY_URL: environ.get("BUDDY_SCM_URL"),
        git.COMMIT_SHA: environ.get("BUDDY_EXECUTION_REVISION"),
        git.BRANCH: environ.get("BUDDY_EXECUTION_BRANCH"),
        git.PULL_REQUEST_BASE_BRANCH: environ.get("BUDDY_RUN_PR_BASE_BRANCH"),
        git.TAG: environ.get("BUDDY_EXECUTION_TAG"),
        git.COMMIT_MESSAGE: environ.get("BUDDY_EXECUTION_REVISION_MESSAGE"),
        git.COMMIT_COMMITTER_NAME: environ.get("BUDDY_EXECUTION_REVISION_COMMITTER_NAME"),
        git.COMMIT_COMMITTER_EMAIL: environ.get("BUDDY_EXECUTION_REVISION_COMMITTER_EMAIL"),
        git.PULL_REQUEST_NUMBER: environ.get("BUDDY_RUN_PR_NO"),
    }


def extract_codebuild(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from codebuild environments."""

    tags = {}

    # AWS Codepipeline
    if "CODEBUILD_INITIATOR" in environ:
        codebuild_initiator = environ.get("CODEBUILD_INITIATOR")
        if codebuild_initiator and codebuild_initiator.startswith("codepipeline"):
            tags.update(
                {
                    PROVIDER_NAME: "awscodepipeline",
                    JOB_ID: environ.get("DD_ACTION_EXECUTION_ID"),
                    PIPELINE_ID: environ.get("DD_PIPELINE_EXECUTION_ID"),
                    _CI_ENV_VARS: json.dumps(
                        {
                            "CODEBUILD_BUILD_ARN": environ.get("CODEBUILD_BUILD_ARN"),
                            "DD_PIPELINE_EXECUTION_ID": environ.get("DD_PIPELINE_EXECUTION_ID"),
                            "DD_ACTION_EXECUTION_ID": environ.get("DD_ACTION_EXECUTION_ID"),
                        },
                        separators=(",", ":"),
                    ),
                }
            )

    return tags


def extract_drone(environ: MutableMapping[str, str]) -> dict[str, Optional[str]]:
    """Extract CI tags from Drone environ."""
    repository = environ.get("DRONE_GIT_HTTP_URL")
    if not repository and environ.get("DRONE_REPO"):
        repository = "https://github.com/{0}.git".format(environ.get("DRONE_REPO"))
    return {
        PROVIDER_NAME: "drone",
        STAGE_NAME: environ.get("DRONE_STAGE_NAME"),
        JOB_NAME: environ.get("DRONE_STEP_NAME"),
        PIPELINE_NUMBER: environ.get("DRONE_BUILD_NUMBER"),
        PIPELINE_URL: environ.get("DRONE_BUILD_LINK"),
        WORKSPACE_PATH: environ.get("DRONE_WORKSPACE"),
        git.BRANCH: environ.get("DRONE_BRANCH") or environ.get("DRONE_COMMIT_BRANCH"),
        git.PULL_REQUEST_BASE_BRANCH: environ.get("DRONE_TARGET_BRANCH"),
        git.COMMIT_SHA: environ.get("DRONE_COMMIT_SHA"),
        git.REPOSITORY_URL: repository,
        git.TAG: environ.get("DRONE_TAG"),
        git.COMMIT_AUTHOR_NAME: environ.get("DRONE_COMMIT_AUTHOR_NAME"),
        git.COMMIT_AUTHOR_EMAIL: environ.get("DRONE_COMMIT_AUTHOR_EMAIL"),
        git.COMMIT_MESSAGE: environ.get("DRONE_COMMIT_MESSAGE"),
        git.PULL_REQUEST_NUMBER: environ.get("DRONE_PULL_REQUEST"),
    }


PROVIDERS = (
    ("APPVEYOR", extract_appveyor),
    ("TF_BUILD", extract_azure_pipelines),
    ("BITBUCKET_COMMIT", extract_bitbucket),
    ("BUILDKITE", extract_buildkite),
    ("CIRCLECI", extract_circle_ci),
    ("CF_BUILD_ID", extract_codefresh),
    ("GITHUB_SHA", extract_github_actions),
    ("GITLAB_CI", extract_gitlab),
    ("JENKINS_URL", extract_jenkins),
    ("TEAMCITY_VERSION", extract_teamcity),
    ("TRAVIS", extract_travis),
    ("BITRISE_BUILD_SLUG", extract_bitrise),
    ("BUDDY", extract_buddy),
    ("CODEBUILD_INITIATOR", extract_codebuild),
    ("DRONE", extract_drone),
)
