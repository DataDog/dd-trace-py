ENV_KEY = "CIRCLECI"


def match(env):
    return env.get(ENV_KEY) is not None


def extract(env):
    return dict(
        provider_name="circleci",
        repository_url=env.get("CIRCLE_REPOSITORY_URL"),
        commit_sha=env.get("CIRCLE_SHA1"),
        workspace_path=env.get("CIRCLE_WORKING_DIRECTORY"),
        pipeline_number=env.get("CIRCLE_BUILD_NUM"),
        pipeline_url=env.get("CIRCLE_BUILD_URL"),
        branch=env.get("CIRCLE_BRANCH"),
    )
