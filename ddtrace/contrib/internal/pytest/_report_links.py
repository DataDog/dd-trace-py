def _build_test_commit_redirect_url():
    from urllib.parse import quote

    from ddtrace import config as ddconfig
    from ddtrace.ext import ci
    from ddtrace.internal.ci_visibility import CIVisibility

    tags = CIVisibility.get_instance()._tags
    settings = CIVisibility.get_session_settings()

    repo_url = tags.get(ci.git.REPOSITORY_URL)
    branch = tags.get(ci.git.BRANCH)
    commit_sha = tags.get(ci.git.COMMIT_SHA)
    service = settings.test_service
    env = ddconfig.env

    if not (repo_url and branch and commit_sha and service):
        return None

    DATADOG_BASE_URL = "https://app.datadoghq.com"
    url_format = f"{DATADOG_BASE_URL}/ci/redirect/tests/{repo_url}/-/{service}/-/{branch}/-/{commit_sha}"
    url = url_format.format(
        repo_url=quote(repo_url, safe=""),
        service=quote(service, safe=""),
        branch=quote(branch, safe=""),
        commit_sha=quote(commit_sha, safe=""),
    )
    if env:
        url += f"?env={env}".format(env=quote(env, safe=""))

    return url


def _build_test_runs_url():
    from urllib.parse import quote

    from ddtrace import config as ddconfig
    from ddtrace.ext import ci
    from ddtrace.internal.ci_visibility import CIVisibility

    DATADOG_BASE_URL = "https://app.datadoghq.com"

    tags = CIVisibility.get_instance()._tags
    ci_job_name = tags.get(ci.JOB_NAME)
    ci_pipeline_id = tags.get(ci.PIPELINE_ID)

    if ci_pipeline_id and ci_job_name:
        return (
            DATADOG_BASE_URL
            + "/ci/test-runs?query=%40ci.job.name%3A{}%20%40ci.pipeline.id%3A{}&index=citest".format(  # noqa: E501
                quote(ci_job_name, safe=""), quote(ci_pipeline_id, safe="")
            )
        )


def print_test_report_links(terminalreporter):
    redirect_test_commit_url = _build_test_commit_redirect_url()
    test_runs_url = _build_test_runs_url()

    if not (redirect_test_commit_url or test_runs_url):
        return

    terminalreporter.section("Datadog Test Reports", cyan=True, bold=True)
    terminalreporter.line("View detailed reports in Datadog (they may take a few minutes to become available):")

    if redirect_test_commit_url:
        terminalreporter.line("")
        terminalreporter.line("* Commit report:")
        terminalreporter.line(f"  → {redirect_test_commit_url}")

    if test_runs_url:
        terminalreporter.line("")
        terminalreporter.line("* Test runs report:")
        terminalreporter.line(f"  → {test_runs_url}")
