ENV_KEY = "TEAMCITY_VERSION"


def extract(env):
    return dict(
        provider_name="teamcity",
        repository_url=env.get("BUILD_VCS_URL"),
        commit_sha=env.get("BUILD_VCS_NUMBER"),
        workspace_path=env.get("BUILD_CHECKOUTDIR"),
        pipeline_id=env.get("BUILD_ID"),
        pipeline_number=env.get("BUILD_NUMBER"),
        pipeline_url=(
            "{0}/viewLog.html?buildId={1}".format(env.get("SERVER_URL"), env.get("BUILD_ID"))
            if env.get("SERVER_URL") and env.get("BUILD_ID")
            else None
        ),
    )
