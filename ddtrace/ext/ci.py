"""
tags for common CI attributes
"""
import os
import re

from . import git

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

_RE_TAGS = re.compile(r"^tags/")
_RE_BRANCH_PREFIX = re.compile(r"^(refs/(heads/)|origin/)?")


def tags(env=None):
    env = os.environ if env is None else env
    for key, extract in PROVIDERS:
        if key in env:
            tags = extract(env)

            # Post process special cases
            branch = tags.get(git.BRANCH)
            if branch:
                tags[git.BRANCH] = _RE_TAGS.sub("", _RE_BRANCH_PREFIX.sub("", branch))
            tag = tags.get(git.TAG)
            if tag:
                tags[git.TAG] = _RE_TAGS.sub("", _RE_BRANCH_PREFIX.sub("", tag))
            tags[git.DEPRECATED_COMMIT_SHA] = tags.get(git.COMMIT_SHA)
            workspace_path = tags.get(WORKSPACE_PATH)
            if workspace_path:
                tags[WORKSPACE_PATH] = os.path.expanduser(workspace_path)
            tags = {k: v for k, v in tags.items() if v is not None}

            return tags
    return {}


def extract_appveyor(env):
    return {
        PROVIDER_NAME: "appveyor",
        git.REPOSITORY_URL: env.get("APPVEYOR_REPO_NAME"),
        git.COMMIT_SHA: env.get("APPVEYOR_REPO_COMMIT"),
        WORKSPACE_PATH: env.get("APPVEYOR_BUILD_FOLDER"),
        PIPELINE_ID: env.get("APPVEYOR_BUILD_ID"),
        PIPELINE_NUMBER: env.get("APPVEYOR_BUILD_NUMBER"),
        PIPELINE_URL: "https://ci.appveyor.com/project/{0}/builds/{1}".format(
            env.get("APPVEYOR_PROJECT_SLUG"), env.get("APPVEYOR_BUILD_ID")
        ),
        git.BRANCH: env.get("APPVEYOR_PULL_REQUEST_HEAD_REPO_BRANCH") or env.get("APPVEYOR_REPO_BRANCH"),
    }


def extract_azure_pipelines(env):
    print(env.get("SYSTEM_TEAMFOUNDATIONSERVERURI"), env.get("SYSTEM_TEAMPROJECT"), env.get("BUILD_BUILDID"))
    if env.get("SYSTEM_TEAMFOUNDATIONSERVERURI") and env.get("SYSTEM_TEAMPROJECT") and env.get("BUILD_BUILDID"):
        base_url = "{0}{1}/_build/results?buildId={2}".format(
            env.get("SYSTEM_TEAMFOUNDATIONSERVERURI"), env.get("SYSTEM_TEAMPROJECT"), env.get("BUILD_BUILDID")
        )
        pipeline_url = base_url + "&_a=summary"
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
        git.REPOSITORY_URL: env.get("SYSTEM_PULLREQUEST_SOURCEREPOSITORYURI") or  env.get("BUILD_REPOSITORY_URI"),
        git.COMMIT_SHA: env.get("SYSTEM_PULLREQUEST_SOURCECOMMITID") or env.get("BUILD_SOURCEVERSION"),
        git.BRANCH: branch_or_tag if "tags/" not in branch_or_tag else None,
        git.TAG: branch_or_tag if "tags/" in branch_or_tag else None,
    }


def extract_bitbucket(env):
    return {
        PROVIDER_NAME: "bitbucketpipelines",
        git.REPOSITORY_URL: env.get("BITBUCKET_GIT_SSH_ORIGIN"),
        git.COMMIT_SHA: env.get("BITBUCKET_COMMIT"),
        WORKSPACE_PATH: env.get("BITBUCKET_CLONE_DIR"),
        PIPELINE_ID: env.get("BITBUCKET_PIPELINE_UUID"),
        PIPELINE_NUMBER: env.get("BITBUCKET_BUILD_NUMBER"),
    }


def extract_buildkite(env):
    return {
        PROVIDER_NAME: "buildkite",
        git.REPOSITORY_URL: env.get("BUILDKITE_REPO"),
        git.COMMIT_SHA: env.get("BUILDKITE_COMMIT"),
        WORKSPACE_PATH: env.get("BUILDKITE_BUILD_CHECKOUT_PATH"),
        PIPELINE_ID: env.get("BUILDKITE_BUILD_ID"),
        PIPELINE_NUMBER: env.get("BUILDKITE_BUILD_NUMBER"),
        PIPELINE_URL: env.get("BUILDKITE_BUILD_URL"),
        git.BRANCH: env.get("BUILDKITE_BRANCH"),
    }


def extract_circle_ci(env):
    return {
        PROVIDER_NAME: "circleci",
        git.REPOSITORY_URL: env.get("CIRCLE_REPOSITORY_URL"),
        git.COMMIT_SHA: env.get("CIRCLE_SHA1"),
        WORKSPACE_PATH: env.get("CIRCLE_WORKING_DIRECTORY"),
        PIPELINE_NUMBER: env.get("CIRCLE_BUILD_NUM"),
        PIPELINE_URL: env.get("CIRCLE_BUILD_URL"),
        git.BRANCH: env.get("CIRCLE_BRANCH"),
    }


def extract_github_actions(env):
    return {
        PROVIDER_NAME: "github",
        git.REPOSITORY_URL: env.get("GITHUB_REPOSITORY"),
        git.COMMIT_SHA: env.get("GITHUB_SHA"),
        WORKSPACE_PATH: env.get("GITHUB_WORKSPACE"),
        PIPELINE_ID: env.get("GITHUB_RUN_ID"),
        PIPELINE_NUMBER: env.get("GITHUB_RUN_NUMBER"),
        PIPELINE_URL: "{0}/commit/{1}/checks".format(env.get("GITHUB_REPOSITORY"), env.get("GITHUB_SHA")),
        git.BRANCH: env.get("GITHUB_REF"),
    }


def extract_gitlab(env):
    return {
        PROVIDER_NAME: "gitlab",
        git.REPOSITORY_URL: env.get("CI_REPOSITORY_URL"),
        git.COMMIT_SHA: env.get("CI_COMMIT_SHA"),
        WORKSPACE_PATH: env.get("CI_PROJECT_DIR"),
        PIPELINE_ID: env.get("CI_PIPELINE_ID"),
        PIPELINE_NUMBER: env.get("CI_PIPELINE_IID"),
        PIPELINE_URL: env.get("CI_PIPELINE_URL"),
        JOB_URL: env.get("CI_JOB_URL"),
        git.BRANCH: env.get("CI_COMMIT_BRANCH") or env.get("CI_COMMIT_REF_NAME"),
    }


def extract_jenkins(env):
    return {
        PROVIDER_NAME: "jenkins",
        git.REPOSITORY_URL: env.get("GIT_URL"),
        git.COMMIT_SHA: env.get("GIT_COMMIT"),
        WORKSPACE_PATH: env.get("WORKSPACE"),
        PIPELINE_ID: env.get("BUILD_ID"),
        PIPELINE_NUMBER: env.get("BUILD_NUMBER"),
        PIPELINE_URL: env.get("BUILD_URL"),
        JOB_URL: env.get("JOB_URL"),
        git.BRANCH: env.get("GIT_BRANCH"),
    }


def extract_teamcity(env):
    return {
        PROVIDER_NAME: "teamcity",
        git.REPOSITORY_URL: env.get("BUILD_VCS_URL"),
        git.COMMIT_SHA: env.get("BUILD_VCS_NUMBER"),
        WORKSPACE_PATH: env.get("BUILD_CHECKOUTDIR"),
        PIPELINE_ID: env.get("BUILD_ID"),
        PIPELINE_NUMBER: env.get("BUILD_NUMBER"),
        PIPELINE_URL: (
            "{0}/viewLog.html?buildId={1}".format(env.get("SERVER_URL"), env.get("BUILD_ID"))
            if env.get("SERVER_URL") and env.get("BUILD_ID")
            else None
        ),
    }


def extract_travis(env):
    return {
        PROVIDER_NAME: "travis",
        git.REPOSITORY_URL: env.get("TRAVIS_REPO_SLUG"),
        git.COMMIT_SHA: env.get("TRAVIS_COMMIT"),
        WORKSPACE_PATH: env.get("TRAVIS_BUILD_DIR"),
        PIPELINE_ID: env.get("TRAVIS_BUILD_ID"),
        PIPELINE_NUMBER: env.get("TRAVIS_BUILD_NUMBER"),
        PIPELINE_URL: env.get("TRAVIS_BUILD_WEB_URL"),
        JOB_URL: env.get("TRAVIS_JOB_WEB_URL"),
        git.BRANCH: env.get("TRAVIS_PULL_REQUEST_BRANCH") or env.get("TRAVIS_BRANCH"),
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
)
