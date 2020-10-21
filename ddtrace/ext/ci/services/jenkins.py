import re

_RE_ORIGIN = re.compile(r"^origin/")


ENV_KEY = "JENKINS_URL"


def match(env):
    return env.get(ENV_KEY) is not None


def extract(env):
    return dict(
        provider_name="jenkins",
        repository_url=env.get("GIT_URL"),
        commit_sha=env.get("GIT_COMMIT"),
        workspace_path=env.get("WORKSPACE"),
        pipeline_id=env.get("BUILD_ID"),
        pipeline_number=env.get("BUILD_NUMBER"),
        pipeline_url=env.get("BUILD_URL"),
        job_url=env.get("JOB_URL"),
        branch=_RE_ORIGIN.sub("", env.get("GIT_BRANCH")),
    )
