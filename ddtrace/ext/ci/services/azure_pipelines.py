ENV_KEY = "TF_BUILD"


def match(env):
    return env.get(ENV_KEY) is not None


def extract(env):
    return dict(
        provider_name="azurepipelines",
        workspace_path=env.get("BUILD_SOURCESDIRECTORY"),
        pipeline_id=env.get("BUILD_BUILDID"),
        pipeline_name=env.get("BUILD_DEFINITIONNAME"),
        pipeline_number=env.get("BUILD_BUILDNUMBER"),
        pipeline_url=all(
            (env.get("SYSTEM_TEAMFOUNDATIONCOLLECTIONURI"), env.get("SYSTEM_TEAMPROJECT"), env.get("BUILD_BUILDID"))
        )
        and "{0}{1}/_build/results?buildId={2}&_a=summary".format(
            env.get("SYSTEM_TEAMFOUNDATIONCOLLECTIONURI"), env.get("SYSTEM_TEAMPROJECT"), env.get("BUILD_BUILDID")
        )
        or None,
        repository_url=env.get("BUILD_REPOSITORY_URI"),
        commit_sha=env.get("SYSTEM_PULLREQUEST_SOURCECOMMITID") or env.get("BUILD_SOURCEVERSION"),
        branch=(
            env.get("SYSTEM_PULLREQUEST_SOURCEBRANCH")
            or env.get("BUILD_SOURCEBRANCH")
            or env.get("BUILD_SOURCEBRANCHNAME")
        ),
    )
