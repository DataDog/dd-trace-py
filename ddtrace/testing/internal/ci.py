import json
import logging
import re
import typing as t

from ddtestpy.internal import git
from ddtestpy.internal.git import GitTag
from ddtestpy.internal.utils import _filter_sensitive_info


log = logging.getLogger(__name__)


class CITag:
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

    # CI Node Name
    NODE_NAME = "ci.node.name"

    # CI Node Labels
    NODE_LABELS = "ci.node.labels"

    # Workspace Path
    WORKSPACE_PATH = "ci.workspace_path"

    # CI Visibility env vars used for pipeline correlation ID
    _CI_ENV_VARS = "_dd.ci.env_vars"


TProviderFunction = t.Callable[[t.MutableMapping[str, str]], t.Dict[str, t.Optional[str]]]
PROVIDERS: t.List[t.Tuple[str, TProviderFunction]] = []


def register_provider(key: str) -> t.Callable[[TProviderFunction], TProviderFunction]:
    """
    Register a handler to extract tags from the environment for a given CI provider.

    The handler will be used if the environment contains an environment variable named by `key`.
    """

    def decorator(f: TProviderFunction) -> TProviderFunction:
        PROVIDERS.append((key, f))
        return f

    return decorator


def get_ci_tags(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract tags from CI  provider environment variables."""
    for key, extract in PROVIDERS:
        if key in env:
            return extract(env)

    return {}


@register_provider("APPVEYOR")
def extract_appveyor(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from Appveyor environ."""
    url = "https://ci.appveyor.com/project/{0}/builds/{1}".format(
        env.get("APPVEYOR_REPO_NAME"), env.get("APPVEYOR_BUILD_ID")
    )
    if env.get("APPVEYOR_REPO_PROVIDER") == "github":
        repository: t.Optional[str] = "https://github.com/{0}.git".format(env.get("APPVEYOR_REPO_NAME"))
        commit = env.get("APPVEYOR_REPO_COMMIT")
        branch = env.get("APPVEYOR_PULL_REQUEST_HEAD_REPO_BRANCH") or env.get("APPVEYOR_REPO_BRANCH")
        tag = env.get("APPVEYOR_REPO_TAG_NAME")
    else:
        repository = commit = branch = tag = None

    commit_message = env.get("APPVEYOR_REPO_COMMIT_MESSAGE")
    if commit_message:
        extended = env.get("APPVEYOR_REPO_COMMIT_MESSAGE_EXTENDED")
        if extended:
            commit_message += "\n" + extended

    return {
        CITag.PROVIDER_NAME: "appveyor",
        GitTag.REPOSITORY_URL: repository,
        GitTag.COMMIT_SHA: commit,
        CITag.WORKSPACE_PATH: env.get("APPVEYOR_BUILD_FOLDER"),
        CITag.PIPELINE_ID: env.get("APPVEYOR_BUILD_ID"),
        CITag.PIPELINE_NAME: env.get("APPVEYOR_REPO_NAME"),
        CITag.PIPELINE_NUMBER: env.get("APPVEYOR_BUILD_NUMBER"),
        CITag.PIPELINE_URL: url,
        CITag.JOB_URL: url,
        GitTag.BRANCH: branch,
        GitTag.TAG: tag,
        GitTag.COMMIT_MESSAGE: commit_message,
        GitTag.COMMIT_AUTHOR_NAME: env.get("APPVEYOR_REPO_COMMIT_AUTHOR"),
        GitTag.COMMIT_AUTHOR_EMAIL: env.get("APPVEYOR_REPO_COMMIT_AUTHOR_EMAIL"),
    }


@register_provider("TF_BUILD")
def extract_azure_pipelines(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from Azure pipelines environ."""
    if env.get("SYSTEM_TEAMFOUNDATIONSERVERURI") and env.get("SYSTEM_TEAMPROJECTID") and env.get("BUILD_BUILDID"):
        base_url: t.Optional[str] = "{0}{1}/_build/results?buildId={2}".format(
            env.get("SYSTEM_TEAMFOUNDATIONSERVERURI"), env.get("SYSTEM_TEAMPROJECTID"), env.get("BUILD_BUILDID")
        )
        pipeline_url = base_url
        job_url = base_url + "&view=logs&j={0}&t={1}".format(env.get("SYSTEM_JOBID"), env.get("SYSTEM_TASKINSTANCEID"))  # type: ignore
    else:
        pipeline_url = job_url = None

    return {
        CITag.PROVIDER_NAME: "azurepipelines",
        CITag.WORKSPACE_PATH: env.get("BUILD_SOURCESDIRECTORY"),
        CITag.PIPELINE_ID: env.get("BUILD_BUILDID"),
        CITag.PIPELINE_NAME: env.get("BUILD_DEFINITIONNAME"),
        CITag.PIPELINE_NUMBER: env.get("BUILD_BUILDID"),
        CITag.PIPELINE_URL: pipeline_url,
        CITag.JOB_URL: job_url,
        GitTag.REPOSITORY_URL: env.get("SYSTEM_PULLREQUEST_SOURCEREPOSITORYURI") or env.get("BUILD_REPOSITORY_URI"),
        GitTag.COMMIT_SHA: env.get("SYSTEM_PULLREQUEST_SOURCECOMMITID") or env.get("BUILD_SOURCEVERSION"),
        GitTag.BRANCH: env.get("SYSTEM_PULLREQUEST_SOURCEBRANCH")
        or env.get("BUILD_SOURCEBRANCH")
        or env.get("BUILD_SOURCEBRANCHNAME"),
        GitTag.COMMIT_MESSAGE: env.get("BUILD_SOURCEVERSIONMESSAGE"),
        GitTag.COMMIT_AUTHOR_NAME: env.get("BUILD_REQUESTEDFORID"),
        GitTag.COMMIT_AUTHOR_EMAIL: env.get("BUILD_REQUESTEDFOREMAIL"),
        CITag.STAGE_NAME: env.get("SYSTEM_STAGEDISPLAYNAME"),
        CITag.JOB_NAME: env.get("SYSTEM_JOBDISPLAYNAME"),
        CITag._CI_ENV_VARS: json.dumps(
            {
                "SYSTEM_TEAMPROJECTID": env.get("SYSTEM_TEAMPROJECTID"),
                "BUILD_BUILDID": env.get("BUILD_BUILDID"),
                "SYSTEM_JOBID": env.get("SYSTEM_JOBID"),
            },
            separators=(",", ":"),
        ),
    }


@register_provider("BITBUCKET_COMMIT")
def extract_bitbucket(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from Bitbucket environ."""
    url = "https://bitbucket.org/{0}/addon/pipelines/home#!/results/{1}".format(
        env.get("BITBUCKET_REPO_FULL_NAME"), env.get("BITBUCKET_BUILD_NUMBER")
    )
    return {
        GitTag.BRANCH: env.get("BITBUCKET_BRANCH"),
        GitTag.COMMIT_SHA: env.get("BITBUCKET_COMMIT"),
        GitTag.REPOSITORY_URL: env.get("BITBUCKET_GIT_SSH_ORIGIN") or env.get("BITBUCKET_GIT_HTTP_ORIGIN"),
        GitTag.TAG: env.get("BITBUCKET_TAG"),
        CITag.JOB_URL: url,
        CITag.PIPELINE_ID: env.get("BITBUCKET_PIPELINE_UUID", "").strip("{}}") or None,  # noqa: B005
        CITag.PIPELINE_NAME: env.get("BITBUCKET_REPO_FULL_NAME"),
        CITag.PIPELINE_NUMBER: env.get("BITBUCKET_BUILD_NUMBER"),
        CITag.PIPELINE_URL: url,
        CITag.PROVIDER_NAME: "bitbucket",
        CITag.WORKSPACE_PATH: env.get("BITBUCKET_CLONE_DIR"),
    }


@register_provider("BUILDKITE")
def extract_buildkite(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from Buildkite environ."""
    # Get all keys which start with BUILDKITE_AGENT_META_DATA_x
    node_label_list: t.List[str] = []
    buildkite_agent_meta_data_prefix = "BUILDKITE_AGENT_META_DATA_"
    for env_variable in env:
        if env_variable.startswith(buildkite_agent_meta_data_prefix):
            key = env_variable.replace(buildkite_agent_meta_data_prefix, "").lower()
            value = env.get(env_variable)
            node_label_list.append("{}:{}".format(key, value))
    return {
        GitTag.BRANCH: env.get("BUILDKITE_BRANCH"),
        GitTag.COMMIT_SHA: env.get("BUILDKITE_COMMIT"),
        GitTag.REPOSITORY_URL: env.get("BUILDKITE_REPO"),
        GitTag.TAG: env.get("BUILDKITE_TAG"),
        CITag.PIPELINE_ID: env.get("BUILDKITE_BUILD_ID"),
        CITag.PIPELINE_NAME: env.get("BUILDKITE_PIPELINE_SLUG"),
        CITag.PIPELINE_NUMBER: env.get("BUILDKITE_BUILD_NUMBER"),
        CITag.PIPELINE_URL: env.get("BUILDKITE_BUILD_URL"),
        CITag.JOB_URL: "{0}#{1}".format(env.get("BUILDKITE_BUILD_URL"), env.get("BUILDKITE_JOB_ID")),
        CITag.PROVIDER_NAME: "buildkite",
        CITag.WORKSPACE_PATH: env.get("BUILDKITE_BUILD_CHECKOUT_PATH"),
        GitTag.COMMIT_MESSAGE: env.get("BUILDKITE_MESSAGE"),
        GitTag.COMMIT_AUTHOR_NAME: env.get("BUILDKITE_BUILD_AUTHOR"),
        GitTag.COMMIT_AUTHOR_EMAIL: env.get("BUILDKITE_BUILD_AUTHOR_EMAIL"),
        GitTag.COMMIT_COMMITTER_NAME: env.get("BUILDKITE_BUILD_CREATOR"),
        GitTag.COMMIT_COMMITTER_EMAIL: env.get("BUILDKITE_BUILD_CREATOR_EMAIL"),
        CITag._CI_ENV_VARS: json.dumps(
            {
                "BUILDKITE_BUILD_ID": env.get("BUILDKITE_BUILD_ID"),
                "BUILDKITE_JOB_ID": env.get("BUILDKITE_JOB_ID"),
            },
            separators=(",", ":"),
        ),
        CITag.NODE_LABELS: json.dumps(node_label_list, separators=(",", ":")),
        CITag.NODE_NAME: env.get("BUILDKITE_AGENT_ID"),
    }


@register_provider("CIRCLECI")
def extract_circle_ci(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from CircleCI environ."""
    return {
        GitTag.BRANCH: env.get("CIRCLE_BRANCH"),
        GitTag.COMMIT_SHA: env.get("CIRCLE_SHA1"),
        GitTag.REPOSITORY_URL: env.get("CIRCLE_REPOSITORY_URL"),
        GitTag.TAG: env.get("CIRCLE_TAG"),
        CITag.PIPELINE_ID: env.get("CIRCLE_WORKFLOW_ID"),
        CITag.PIPELINE_NAME: env.get("CIRCLE_PROJECT_REPONAME"),
        CITag.PIPELINE_NUMBER: env.get("CIRCLE_BUILD_NUM"),
        CITag.PIPELINE_URL: "https://app.circleci.com/pipelines/workflows/{0}".format(env.get("CIRCLE_WORKFLOW_ID")),
        CITag.JOB_URL: env.get("CIRCLE_BUILD_URL"),
        CITag.JOB_NAME: env.get("CIRCLE_JOB"),
        CITag.PROVIDER_NAME: "circleci",
        CITag.WORKSPACE_PATH: env.get("CIRCLE_WORKING_DIRECTORY"),
        CITag._CI_ENV_VARS: json.dumps(
            {
                "CIRCLE_WORKFLOW_ID": env.get("CIRCLE_WORKFLOW_ID"),
                "CIRCLE_BUILD_NUM": env.get("CIRCLE_BUILD_NUM"),
            },
            separators=(",", ":"),
        ),
    }


@register_provider("CF_BUILD_ID")
def extract_codefresh(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from Codefresh environ."""
    build_id = env.get("CF_BUILD_ID")
    return {
        GitTag.BRANCH: env.get("CF_BRANCH"),
        CITag.PIPELINE_ID: build_id,
        CITag.PIPELINE_NAME: env.get("CF_PIPELINE_NAME"),
        CITag.PIPELINE_URL: env.get("CF_BUILD_URL"),
        CITag.JOB_NAME: env.get("CF_STEP_NAME"),
        CITag.PROVIDER_NAME: "codefresh",
        CITag._CI_ENV_VARS: json.dumps(
            {"CF_BUILD_ID": build_id},
            separators=(",", ":"),
        ),
    }


@register_provider("GITHUB_SHA")
def extract_github_actions(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from Github environ."""
    github_server_url = _filter_sensitive_info(env.get("GITHUB_SERVER_URL"))
    github_repository = env.get("GITHUB_REPOSITORY")
    git_commit_sha = env.get("GITHUB_SHA")
    github_run_id = env.get("GITHUB_RUN_ID")
    run_attempt = env.get("GITHUB_RUN_ATTEMPT")

    pipeline_url = "{0}/{1}/actions/runs/{2}".format(
        github_server_url,
        github_repository,
        github_run_id,
    )

    git_commit_head_sha = None
    if "GITHUB_EVENT_PATH" in env:
        try:
            with open(env["GITHUB_EVENT_PATH"]) as f:
                github_event_data = json.load(f)
                git_commit_head_sha = github_event_data.get("pull_request", {}).get("head", {}).get("sha")
        except Exception as e:
            log.error("Failed to read or parse GITHUB_EVENT_PATH: %s", e)

    env_vars = {
        "GITHUB_SERVER_URL": github_server_url,
        "GITHUB_REPOSITORY": github_repository,
        "GITHUB_RUN_ID": github_run_id,
    }
    if run_attempt:
        env_vars["GITHUB_RUN_ATTEMPT"] = run_attempt
        pipeline_url = "{0}/attempts/{1}".format(pipeline_url, run_attempt)

    return {
        GitTag.BRANCH: env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF"),
        GitTag.COMMIT_SHA: git_commit_sha,
        GitTag.REPOSITORY_URL: "{0}/{1}.git".format(github_server_url, github_repository),
        GitTag.COMMIT_HEAD_SHA: git_commit_head_sha,
        CITag.JOB_URL: "{0}/{1}/commit/{2}/checks".format(github_server_url, github_repository, git_commit_sha),
        CITag.PIPELINE_ID: github_run_id,
        CITag.PIPELINE_NAME: env.get("GITHUB_WORKFLOW"),
        CITag.PIPELINE_NUMBER: env.get("GITHUB_RUN_NUMBER"),
        CITag.PIPELINE_URL: pipeline_url,
        CITag.JOB_NAME: env.get("GITHUB_JOB"),
        CITag.PROVIDER_NAME: "github",
        CITag.WORKSPACE_PATH: env.get("GITHUB_WORKSPACE"),
        CITag._CI_ENV_VARS: json.dumps(env_vars, separators=(",", ":")),
    }


@register_provider("GITLAB_CI")
def extract_gitlab(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from Gitlab environ."""
    author = env.get("CI_COMMIT_AUTHOR")
    author_name = None
    author_email = None
    if author:
        # Extract name and email from `author` which is in the form "name <email>"
        author_name, author_email = author.strip("> ").split(" <")
    commit_timestamp = env.get("CI_COMMIT_TIMESTAMP")
    return {
        GitTag.BRANCH: env.get("CI_COMMIT_REF_NAME"),
        GitTag.COMMIT_SHA: env.get("CI_COMMIT_SHA"),
        GitTag.REPOSITORY_URL: env.get("CI_REPOSITORY_URL"),
        GitTag.TAG: env.get("CI_COMMIT_TAG"),
        CITag.STAGE_NAME: env.get("CI_JOB_STAGE"),
        CITag.JOB_NAME: env.get("CI_JOB_NAME"),
        CITag.JOB_URL: env.get("CI_JOB_URL"),
        CITag.PIPELINE_ID: env.get("CI_PIPELINE_ID"),
        CITag.PIPELINE_NAME: env.get("CI_PROJECT_PATH"),
        CITag.PIPELINE_NUMBER: env.get("CI_PIPELINE_IID"),
        CITag.PIPELINE_URL: env.get("CI_PIPELINE_URL"),
        CITag.PROVIDER_NAME: "gitlab",
        CITag.WORKSPACE_PATH: env.get("CI_PROJECT_DIR"),
        GitTag.COMMIT_MESSAGE: env.get("CI_COMMIT_MESSAGE"),
        GitTag.COMMIT_AUTHOR_NAME: author_name,
        GitTag.COMMIT_AUTHOR_EMAIL: author_email,
        GitTag.COMMIT_AUTHOR_DATE: commit_timestamp,
        CITag._CI_ENV_VARS: json.dumps(
            {
                "CI_PROJECT_URL": env.get("CI_PROJECT_URL"),
                "CI_PIPELINE_ID": env.get("CI_PIPELINE_ID"),
                "CI_JOB_ID": env.get("CI_JOB_ID"),
            },
            separators=(",", ":"),
        ),
        CITag.NODE_LABELS: env.get("CI_RUNNER_TAGS"),
        CITag.NODE_NAME: env.get("CI_RUNNER_ID"),
    }


@register_provider("JENKINS_URL")
def extract_jenkins(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from Jenkins environ."""
    branch = env.get("GIT_BRANCH", "")
    name = env.get("JOB_NAME")
    if name and branch:
        name = re.sub("/{0}".format(git.normalize_ref(branch)), "", name)
    if name:
        name = "/".join((v for v in name.split("/") if v and "=" not in v))
    node_labels_list: t.List[str] = []
    node_labels_env = env.get("NODE_LABELS")
    if node_labels_env:
        node_labels_list = node_labels_env.split()
    return {
        GitTag.BRANCH: env.get("GIT_BRANCH"),
        GitTag.COMMIT_SHA: env.get("GIT_COMMIT"),
        GitTag.REPOSITORY_URL: env.get("GIT_URL", env.get("GIT_URL_1")),
        CITag.PIPELINE_ID: env.get("BUILD_TAG"),
        CITag.PIPELINE_NAME: name,
        CITag.PIPELINE_NUMBER: env.get("BUILD_NUMBER"),
        CITag.PIPELINE_URL: env.get("BUILD_URL"),
        CITag.PROVIDER_NAME: "jenkins",
        CITag.WORKSPACE_PATH: env.get("WORKSPACE"),
        CITag._CI_ENV_VARS: json.dumps(
            {
                "DD_CUSTOM_TRACE_ID": env.get("DD_CUSTOM_TRACE_ID"),
            },
            separators=(",", ":"),
        ),
        CITag.NODE_LABELS: json.dumps(node_labels_list, separators=(",", ":")),
        CITag.NODE_NAME: env.get("NODE_NAME"),
    }


@register_provider("TEAMCITY_VERSION")
def extract_teamcity(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from Teamcity environ."""
    return {
        CITag.JOB_URL: env.get("BUILD_URL"),
        CITag.JOB_NAME: env.get("TEAMCITY_BUILDCONF_NAME"),
        CITag.PROVIDER_NAME: "teamcity",
    }


@register_provider("TRAVIS")
def extract_travis(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from Travis environ."""
    return {
        GitTag.BRANCH: env.get("TRAVIS_PULL_REQUEST_BRANCH") or env.get("TRAVIS_BRANCH"),
        GitTag.COMMIT_SHA: env.get("TRAVIS_COMMIT"),
        GitTag.REPOSITORY_URL: "https://github.com/{0}.git".format(env.get("TRAVIS_REPO_SLUG")),
        GitTag.TAG: env.get("TRAVIS_TAG"),
        CITag.JOB_URL: env.get("TRAVIS_JOB_WEB_URL"),
        CITag.PIPELINE_ID: env.get("TRAVIS_BUILD_ID"),
        CITag.PIPELINE_NAME: env.get("TRAVIS_REPO_SLUG"),
        CITag.PIPELINE_NUMBER: env.get("TRAVIS_BUILD_NUMBER"),
        CITag.PIPELINE_URL: env.get("TRAVIS_BUILD_WEB_URL"),
        CITag.PROVIDER_NAME: "travisci",
        CITag.WORKSPACE_PATH: env.get("TRAVIS_BUILD_DIR"),
        GitTag.COMMIT_MESSAGE: env.get("TRAVIS_COMMIT_MESSAGE"),
    }


@register_provider("BITRISE_BUILD_SLUG")
def extract_bitrise(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from Bitrise environ."""
    commit = env.get("BITRISE_GIT_COMMIT") or env.get("GIT_CLONE_COMMIT_HASH")
    branch = env.get("BITRISEIO_GIT_BRANCH_DEST") or env.get("BITRISE_GIT_BRANCH")
    if env.get("BITRISE_GIT_MESSAGE"):
        message = env.get("BITRISE_GIT_MESSAGE")
    elif env.get("GIT_CLONE_COMMIT_MESSAGE_SUBJECT") or env.get("GIT_CLONE_COMMIT_MESSAGE_BODY"):
        message = "{0}:\n{1}".format(
            env.get("GIT_CLONE_COMMIT_MESSAGE_SUBJECT"), env.get("GIT_CLONE_COMMIT_MESSAGE_BODY")
        )
    else:
        message = None

    return {
        CITag.PROVIDER_NAME: "bitrise",
        CITag.PIPELINE_ID: env.get("BITRISE_BUILD_SLUG"),
        CITag.PIPELINE_NAME: env.get("BITRISE_TRIGGERED_WORKFLOW_ID"),
        CITag.PIPELINE_NUMBER: env.get("BITRISE_BUILD_NUMBER"),
        CITag.PIPELINE_URL: env.get("BITRISE_BUILD_URL"),
        CITag.WORKSPACE_PATH: env.get("BITRISE_SOURCE_DIR"),
        GitTag.REPOSITORY_URL: env.get("GIT_REPOSITORY_URL"),
        GitTag.COMMIT_SHA: commit,
        GitTag.BRANCH: branch,
        GitTag.TAG: env.get("BITRISE_GIT_TAG"),
        GitTag.COMMIT_MESSAGE: message,
        GitTag.COMMIT_AUTHOR_NAME: env.get("GIT_CLONE_COMMIT_AUTHOR_NAME"),
        GitTag.COMMIT_AUTHOR_EMAIL: env.get("GIT_CLONE_COMMIT_AUTHOR_EMAIL"),
        GitTag.COMMIT_COMMITTER_NAME: env.get("GIT_CLONE_COMMIT_COMMITER_NAME"),
        GitTag.COMMIT_COMMITTER_EMAIL: env.get("GIT_CLONE_COMMIT_COMMITER_NAME"),
    }


@register_provider("BUDDY")
def extract_buddy(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from Buddy environ."""
    return {
        CITag.PROVIDER_NAME: "buddy",
        CITag.PIPELINE_ID: "{0}/{1}".format(env.get("BUDDY_PIPELINE_ID"), env.get("BUDDY_EXECUTION_ID")),
        CITag.PIPELINE_NAME: env.get("BUDDY_PIPELINE_NAME"),
        CITag.PIPELINE_NUMBER: env.get("BUDDY_EXECUTION_ID"),
        CITag.PIPELINE_URL: env.get("BUDDY_EXECUTION_URL"),
        GitTag.REPOSITORY_URL: env.get("BUDDY_SCM_URL"),
        GitTag.COMMIT_SHA: env.get("BUDDY_EXECUTION_REVISION"),
        GitTag.BRANCH: env.get("BUDDY_EXECUTION_BRANCH"),
        GitTag.TAG: env.get("BUDDY_EXECUTION_TAG"),
        GitTag.COMMIT_MESSAGE: env.get("BUDDY_EXECUTION_REVISION_MESSAGE"),
        GitTag.COMMIT_COMMITTER_NAME: env.get("BUDDY_EXECUTION_REVISION_COMMITTER_NAME"),
        GitTag.COMMIT_COMMITTER_EMAIL: env.get("BUDDY_EXECUTION_REVISION_COMMITTER_EMAIL"),
    }


@register_provider("CODEBUILD_INITIATOR")
def extract_codebuild(env: t.MutableMapping[str, str]) -> t.Dict[str, t.Optional[str]]:
    """Extract CI tags from codebuild environments."""
    tags = {}

    # AWS Codepipeline
    if "CODEBUILD_INITIATOR" in env:
        codebuild_initiator = env.get("CODEBUILD_INITIATOR")
        if codebuild_initiator and codebuild_initiator.startswith("codepipeline"):
            tags.update(
                {
                    CITag.PROVIDER_NAME: "awscodepipeline",
                    CITag.PIPELINE_ID: env.get("DD_PIPELINE_EXECUTION_ID"),
                    CITag._CI_ENV_VARS: json.dumps(
                        {
                            "CODEBUILD_BUILD_ARN": env.get("CODEBUILD_BUILD_ARN"),
                            "DD_PIPELINE_EXECUTION_ID": env.get("DD_PIPELINE_EXECUTION_ID"),
                            "DD_ACTION_EXECUTION_ID": env.get("DD_ACTION_EXECUTION_ID"),
                        },
                        separators=(",", ":"),
                    ),
                }
            )

    return tags
