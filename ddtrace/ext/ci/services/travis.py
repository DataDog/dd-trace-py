ENV_KEY = "TRAVIS"


def match(env):
    return env.get(ENV_KEY) is not None


def extract(env):
    return dict(
        provider_name="travis",
        repository_url=env.get("TRAVIS_REPO_SLUG"),
        commit_sha=env.get("TRAVIS_COMMIT"),
        workspace_path=env.get("TRAVIS_BUILD_DIR"),
        pipeline_id=env.get("TRAVIS_BUILD_ID"),
        pipeline_number=env.get("TRAVIS_BUILD_NUMBER"),
        pipeline_url=env.get("TRAVIS_BUILD_WEB_URL"),
        job_url=env.get("TRAVIS_JOB_WEB_URL"),
        branch=env.get("TRAVIS_PULL_REQUEST_BRANCH") or env.get("TRAVIS_BRANCH"),
    )
