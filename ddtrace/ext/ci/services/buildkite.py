ENV_KEY = "BUILDKITE"


def extract(env):
    return dict(
        provider_name="buildkite",
        repository_url=env.get("BUILDKITE_REPO"),
        commit_sha=env.get("BUILDKITE_COMMIT"),
        workspace_path=env.get("BUILDKITE_BUILD_CHECKOUT_PATH"),
        pipeline_id=env.get("BUILDKITE_BUILD_ID"),
        pipeline_number=env.get("BUILDKITE_BUILD_NUMBER"),
        pipeline_url=env.get("BUILDKITE_BUILD_URL"),
        branch=env.get("BUILDKITE_BRANCH"),
    )
