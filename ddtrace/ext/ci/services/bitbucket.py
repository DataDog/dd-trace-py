ENV_KEY = "BITBUCKET_COMMIT"


def match(env):
    return env.get(ENV_KEY) is not None


def extract(env):
    return dict(
        provider_name="bitbucketpipelines",
        repository_url=env.get("BITBUCKET_GIT_SSH_ORIGIN"),
        commit_sha=env.get("BITBUCKET_COMMIT"),
        workspace_path=env.get("BITBUCKET_CLONE_DIR"),
        pipeline_id=env.get("BITBUCKET_PIPELINE_UUID"),
        pipeline_number=env.get("BITBUCKET_BUILD_NUMBER"),
    )
