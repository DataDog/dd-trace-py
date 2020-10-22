ENV_KEY = "GITHUB_SHA"


def extract(env):
    return dict(
        provider_name="github",
        repository_url=env.get("GITHUB_REPOSITORY"),
        commit_sha=env.get("GITHUB_SHA"),
        workspace_path=env.get("GITHUB_WORKSPACE"),
        pipeline_id=env.get("GITHUB_RUN_ID"),
        pipeline_number=env.get("GITHUB_RUN_NUMBER"),
        pipeline_url="{0}/commit/{1}/checks".format(env.get("GITHUB_REPOSITORY"), env.get("GITHUB_SHA")),
        branch=env.get("GITHUB_REF"),
    )
