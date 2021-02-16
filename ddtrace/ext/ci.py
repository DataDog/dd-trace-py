"""
tags for common CI attributes
"""
import os
import re

from . import git


# Stage Name
STAGE_NAME = "ci.stage.name"

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

# Workspace Path
WORKSPACE_PATH = "ci.workspace_path"

_RE_REFS = re.compile(r"^refs/(heads/)?")
_RE_ORIGIN = re.compile(r"^origin/")
_RE_TAGS = re.compile(r"^tags/")
_RE_URL = re.compile(r"(https?://)[^/]*@")


def _normalize_ref(name):
    return _RE_TAGS.sub("", _RE_ORIGIN.sub("", _RE_REFS.sub("", name))) if name is not None else None


def _filter_sensitive_info(url):
    return _RE_URL.sub("\\1", url) if url is not None else None


def tags(env=None):
    env = os.environ if env is None else env
    tags = {}
    for key, extract in PROVIDERS:
        if key in env:
            tags = extract(env)
            break

    tags[git.TAG] = _normalize_ref(tags.get(git.TAG))
    if tags.get(git.TAG) and git.BRANCH in tags:
        del tags[git.BRANCH]
    tags[git.BRANCH] = _normalize_ref(tags.get(git.BRANCH))
    tags[git.REPOSITORY_URL] = _filter_sensitive_info(tags.get(git.REPOSITORY_URL))

    # Expand ~
    workspace_path = tags.get(WORKSPACE_PATH)
    if workspace_path:
        tags[WORKSPACE_PATH] = os.path.expanduser(workspace_path)

    return {k: v for k, v in tags.items() if v is not None}


def extract_appveyor(env):
    url = "https://ci.appveyor.com/project/{0}/builds/{1}".format(
        env.get("APPVEYOR_REPO_NAME"), env.get("APPVEYOR_BUILD_ID")
    )

    if env.get("APPVEYOR_REPO_PROVIDER") == "github":
        repository = "https://github.com/{0}.git".format(env.get("APPVEYOR_REPO_NAME"))
        commit = env.get("APPVEYOR_REPO_COMMIT")
        branch = env.get("APPVEYOR_PULL_REQUEST_HEAD_REPO_BRANCH") or env.get("APPVEYOR_REPO_BRANCH")
        tag = env.get("APPVEYOR_REPO_TAG_NAME")
    else:
        repository = commit = branch = tag = None

    return {
        PROVIDER_NAME: "appveyor",
        git.REPOSITORY_URL: repository,
        git.COMMIT_SHA: commit,
        WORKSPACE_PATH: env.get("APPVEYOR_BUILD_FOLDER"),
        PIPELINE_ID: env.get("APPVEYOR_BUILD_ID"),
        PIPELINE_NAME: env.get("APPVEYOR_REPO_NAME"),
        PIPELINE_NUMBER: env.get("APPVEYOR_BUILD_NUMBER"),
        PIPELINE_URL: url,
        JOB_URL: url,
        git.BRANCH: branch,
        git.TAG: tag,
    }


def extract_azure_pipelines(env):
    if env.get("SYSTEM_TEAMFOUNDATIONSERVERURI") and env.get("SYSTEM_TEAMPROJECTID") and env.get("BUILD_BUILDID"):
        base_url = "{0}{1}/_build/results?buildId={2}".format(
            env.get("SYSTEM_TEAMFOUNDATIONSERVERURI"), env.get("SYSTEM_TEAMPROJECTID"), env.get("BUILD_BUILDID")
        )
        pipeline_url = base_url
        job_url = base_url + "&view=logs&j={0}&t={1}".format(env.get("SYSTEM_JOBID"), env.get("SYSTEM_TASKINSTANCEID"))
    else:
        pipeline_url = job_url = None
    branch_or_tag = (
        env.get("SYSTEM_PULLREQUEST_SOURCEBRANCH") or env.get("BUILD_SOURCEBRANCH") or env.get("BUILD_SOURCEBRANCHNAME")
    )
    return {
        PROVIDER_NAME: "azurepipelines",
        WORKSPACE_PATH: env.get("BUILD_SOURCESDIRECTORY"),
        PIPELINE_ID: env.get("BUILD_BUILDID"),
        PIPELINE_NAME: env.get("BUILD_DEFINITIONNAME"),
        PIPELINE_NUMBER: env.get("BUILD_BUILDID"),
        PIPELINE_URL: pipeline_url,
        JOB_URL: job_url,
        git.REPOSITORY_URL: env.get("SYSTEM_PULLREQUEST_SOURCEREPOSITORYURI") or env.get("BUILD_REPOSITORY_URI"),
        git.COMMIT_SHA: env.get("SYSTEM_PULLREQUEST_SOURCECOMMITID") or env.get("BUILD_SOURCEVERSION"),
        git.BRANCH: branch_or_tag if "tags/" not in branch_or_tag else None,
        git.TAG: branch_or_tag if "tags/" in branch_or_tag else None,
    }


def extract_bitbucket(env):
    url = "https://bitbucket.org/{0}/addon/pipelines/home#!/results/{1}".format(
        env.get("BITBUCKET_REPO_FULL_NAME"), env.get("BITBUCKET_BUILD_NUMBER")
    )
    return {
        git.BRANCH: env.get("BITBUCKET_BRANCH"),
        git.COMMIT_SHA: env.get("BITBUCKET_COMMIT"),
        git.REPOSITORY_URL: env.get("BITBUCKET_GIT_SSH_ORIGIN"),
        git.TAG: env.get("BITBUCKET_TAG"),
        JOB_URL: url,
        PIPELINE_ID: env.get("BITBUCKET_PIPELINE_UUID", "").strip("{}}") or None,
        PIPELINE_NAME: env.get("BITBUCKET_REPO_FULL_NAME"),
        PIPELINE_NUMBER: env.get("BITBUCKET_BUILD_NUMBER"),
        PIPELINE_URL: url,
        PROVIDER_NAME: "bitbucket",
        WORKSPACE_PATH: env.get("BITBUCKET_CLONE_DIR"),
    }


def extract_buildkite(env):
    return {
        git.BRANCH: env.get("BUILDKITE_BRANCH"),
        git.COMMIT_SHA: env.get("BUILDKITE_COMMIT"),
        git.REPOSITORY_URL: env.get("BUILDKITE_REPO"),
        git.TAG: env.get("BUILDKITE_TAG"),
        PIPELINE_ID: env.get("BUILDKITE_BUILD_ID"),
        PIPELINE_NAME: env.get("BUILDKITE_PIPELINE_SLUG"),
        PIPELINE_NUMBER: env.get("BUILDKITE_BUILD_NUMBER"),
        PIPELINE_URL: env.get("BUILDKITE_BUILD_URL"),
        JOB_URL: "{0}#{1}".format(env.get("BUILDKITE_BUILD_URL"), env.get("BUILDKITE_JOB_ID")),
        PROVIDER_NAME: "buildkite",
        WORKSPACE_PATH: env.get("BUILDKITE_BUILD_CHECKOUT_PATH"),
    }


def extract_circle_ci(env):
    return {
        git.BRANCH: env.get("CIRCLE_BRANCH"),
        git.COMMIT_SHA: env.get("CIRCLE_SHA1"),
        git.REPOSITORY_URL: env.get("CIRCLE_REPOSITORY_URL"),
        git.TAG: env.get("CIRCLE_TAG"),
        PIPELINE_ID: env.get("CIRCLE_WORKFLOW_ID"),
        PIPELINE_NAME: env.get("CIRCLE_PROJECT_REPONAME"),
        PIPELINE_NUMBER: env.get("CIRCLE_BUILD_NUM"),
        PIPELINE_URL: env.get("CIRCLE_BUILD_URL"),
        JOB_URL: env.get("CIRCLE_BUILD_URL"),
        PROVIDER_NAME: "circleci",
        WORKSPACE_PATH: env.get("CIRCLE_WORKING_DIRECTORY"),
    }


def extract_github_actions(env):
    branch_or_tag = env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF")
    branch = tag = None
    if "tags/" in branch_or_tag:
        tag = branch_or_tag
    else:
        branch = branch_or_tag
    return {
        git.BRANCH: branch,
        git.COMMIT_SHA: env.get("GITHUB_SHA"),
        git.REPOSITORY_URL: "https://github.com/{0}.git".format(env.get("GITHUB_REPOSITORY")),
        git.TAG: tag,
        JOB_URL: "https://github.com/{0}/commit/{1}/checks".format(env.get("GITHUB_REPOSITORY"), env.get("GITHUB_SHA")),
        PIPELINE_ID: env.get("GITHUB_RUN_ID"),
        PIPELINE_NAME: env.get("GITHUB_WORKFLOW"),
        PIPELINE_NUMBER: env.get("GITHUB_RUN_NUMBER"),
        PIPELINE_URL: "https://github.com/{0}/commit/{1}/checks".format(
            env.get("GITHUB_REPOSITORY"), env.get("GITHUB_SHA")
        ),
        PROVIDER_NAME: "github",
        WORKSPACE_PATH: env.get("GITHUB_WORKSPACE"),
    }


def extract_gitlab(env):
    url = env.get("CI_PIPELINE_URL")
    if url:
        url = re.sub("/-/pipelines/", "/pipelines/", url)
    return {
        git.BRANCH: env.get("CI_COMMIT_BRANCH"),
        git.COMMIT_SHA: env.get("CI_COMMIT_SHA"),
        git.REPOSITORY_URL: env.get("CI_REPOSITORY_URL"),
        git.TAG: env.get("CI_COMMIT_TAG"),
        STAGE_NAME: env.get("CI_JOB_STAGE"),
        JOB_NAME: env.get("CI_JOB_NAME"),
        JOB_URL: env.get("CI_JOB_URL"),
        PIPELINE_ID: env.get("CI_PIPELINE_ID"),
        PIPELINE_NAME: env.get("CI_PROJECT_PATH"),
        PIPELINE_NUMBER: env.get("CI_PIPELINE_IID"),
        PIPELINE_URL: url,
        PROVIDER_NAME: "gitlab",
        WORKSPACE_PATH: env.get("CI_PROJECT_DIR"),
    }


def extract_jenkins(env):
    branch_or_tag = env.get("GIT_BRANCH")
    branch = tag = None
    if "tags/" in branch_or_tag:
        tag = branch_or_tag
    else:
        branch = branch_or_tag
    name = env.get("JOB_NAME")
    if name and branch:
        name = re.sub("/" + _normalize_ref(branch), "", name)
    if name:
        name = "/".join((v for v in name.split("/") if v and "=" not in v))

    return {
        git.BRANCH: branch,
        git.COMMIT_SHA: env.get("GIT_COMMIT"),
        git.REPOSITORY_URL: env.get("GIT_URL"),
        git.TAG: tag,
        PIPELINE_ID: env.get("BUILD_TAG"),
        PIPELINE_NAME: name,
        PIPELINE_NUMBER: env.get("BUILD_NUMBER"),
        PIPELINE_URL: env.get("BUILD_URL"),
        PROVIDER_NAME: "jenkins",
        WORKSPACE_PATH: env.get("WORKSPACE"),
    }


def extract_teamcity(env):
    return {
        git.COMMIT_SHA: env.get("BUILD_VCS_NUMBER"),
        git.REPOSITORY_URL: env.get("BUILD_VCS_URL"),
        PIPELINE_ID: env.get("BUILD_ID"),
        PIPELINE_NUMBER: env.get("BUILD_NUMBER"),
        PIPELINE_URL: (
            "{0}/viewLog.html?buildId={1}".format(env.get("SERVER_URL"), env.get("BUILD_ID"))
            if env.get("SERVER_URL") and env.get("BUILD_ID")
            else None
        ),
        PROVIDER_NAME: "teamcity",
        WORKSPACE_PATH: env.get("BUILD_CHECKOUTDIR"),
    }


def extract_travis(env):
    return {
        git.BRANCH: env.get("TRAVIS_PULL_REQUEST_BRANCH") or env.get("TRAVIS_BRANCH"),
        git.COMMIT_SHA: env.get("TRAVIS_COMMIT"),
        git.REPOSITORY_URL: "https://github.com/{0}.git".format(env.get("TRAVIS_REPO_SLUG")),
        git.TAG: env.get("TRAVIS_TAG"),
        JOB_URL: env.get("TRAVIS_JOB_WEB_URL"),
        PIPELINE_ID: env.get("TRAVIS_BUILD_ID"),
        PIPELINE_NAME: env.get("TRAVIS_REPO_SLUG"),
        PIPELINE_NUMBER: env.get("TRAVIS_BUILD_NUMBER"),
        PIPELINE_URL: env.get("TRAVIS_BUILD_WEB_URL"),
        PROVIDER_NAME: "travisci",
        WORKSPACE_PATH: env.get("TRAVIS_BUILD_DIR"),
    }


def extract_bitrise(env):
    commit = env.get("BITRISE_GIT_COMMIT") or env.get("GIT_CLONE_COMMIT_HASH")
    branch = env.get("BITRISEIO_GIT_BRANCH_DEST") or env.get("BITRISE_GIT_BRANCH")
    return {
        PROVIDER_NAME: "bitrise",
        PIPELINE_ID: env.get("BITRISE_BUILD_SLUG"),
        PIPELINE_NAME: env.get("BITRISE_APP_TITLE"),
        PIPELINE_NUMBER: env.get("BITRISE_BUILD_NUMBER"),
        PIPELINE_URL: env.get("BITRISE_BUILD_URL"),
        WORKSPACE_PATH: env.get("BITRISE_SOURCE_DIR"),
        git.REPOSITORY_URL: env.get("GIT_REPOSITORY_URL"),
        git.COMMIT_SHA: commit,
        git.BRANCH: branch,
        git.TAG: env.get("BITRISE_GIT_TAG"),
    }


PROVIDERS = (
    ("APPVEYOR", extract_appveyor),
    ("TF_BUILD", extract_azure_pipelines),
    ("BITBUCKET_COMMIT", extract_bitbucket),
    ("BUILDKITE", extract_buildkite),
    ("CIRCLECI", extract_circle_ci),
    ("GITHUB_SHA", extract_github_actions),
    ("GITLAB_CI", extract_gitlab),
    ("JENKINS_URL", extract_jenkins),
    ("TEAMCITY_VERSION", extract_teamcity),
    ("TRAVIS", extract_travis),
    ("BITRISE_BUILD_SLUG", extract_bitrise),
)
