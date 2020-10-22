ENV_KEY = "GITLAB_CI"


def extract(env):
    return dict(
        provider_name="gitlab",
        repository_url=env.get("CI_REPOSITORY_URL"),
        commit_sha=env.get("CI_COMMIT_SHA"),
        workspace_path=env.get("CI_PROJECT_DIR"),
        pipeline_id=env.get("CI_PIPELINE_ID"),
        pipeline_number=env.get("CI_PIPELINE_IID"),
        pipeline_url=env.get("CI_PIPELINE_URL"),
        job_url=env.get("CI_JOB_URL"),
        branch=env.get("CI_COMMIT_BRANCH") or env.get("CI_COMMIT_REF_NAME"),
    )
