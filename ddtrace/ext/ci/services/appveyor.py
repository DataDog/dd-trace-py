ENV_KEY = "APPVEYOR"


def extract(env):
    return dict(
        provider_name="appveyor",
        repository_url=env.get("APPVEYOR_REPO_NAME"),
        commit_sha=env.get("APPVEYOR_REPO_COMMIT"),
        workspace_path=env.get("APPVEYOR_BUILD_FOLDER"),
        pipeline_id=env.get("APPVEYOR_BUILD_ID"),
        pipeline_number=env.get("APPVEYOR_BUILD_NUMBER"),
        pipeline_url="https://ci.appveyor.com/project/{0}/builds/{1}".format(
            env.get("APPVEYOR_PROJECT_SLUG"), env.get("APPVEYOR_BUILD_ID")
        ),
        branch=env.get("APPVEYOR_PULL_REQUEST_HEAD_REPO_BRANCH") or env.get("APPVEYOR_REPO_BRANCH"),
    )
