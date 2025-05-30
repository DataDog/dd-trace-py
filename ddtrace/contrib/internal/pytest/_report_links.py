import re
from urllib.parse import quote

from ddtrace import config as ddconfig
from ddtrace.ext import ci
from ddtrace.internal.ci_visibility import CIVisibility


DATADOG_BASE_URL = "https://app.datadoghq.com"

SAFE_FOR_QUERY = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def print_test_report_links(terminalreporter):
    breakpoint()
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


def _build_test_commit_redirect_url():
    tags = CIVisibility.get_ci_tags()
    settings = CIVisibility.get_session_settings()
    env = ddconfig.env

    params = {
        "repo_url": tags.get(ci.git.REPOSITORY_URL),
        "branch": tags.get(ci.git.BRANCH),
        "commit_sha": tags.get(ci.git.COMMIT_SHA),
        "service": settings.test_service,
    }
    if any(v is None for v in params.values()):
        return None

    url_format = "/ci/redirect/tests/{repo_url}/-/{service}/-/{branch}/-/{commit_sha}"
    url = DATADOG_BASE_URL + url_format.format(**{k: quote(v, safe="") for k, v in params.items()})
    if env:
        url += f"?env={env}".format(env=quote(env, safe=""))

    return url


def _quote_for_query(text):
    if SAFE_FOR_QUERY.match(text):
        return text

    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _build_test_runs_url():
    tags = CIVisibility.get_ci_tags()
    ci_job_name = tags.get(ci.JOB_NAME)
    ci_pipeline_id = tags.get(ci.PIPELINE_ID)

    if not (ci_job_name and ci_pipeline_id):
        return None

    query = "@ci.job.name:{} @ci.pipeline.id:{}".format(_quote_for_query(ci_job_name), _quote_for_query(ci_pipeline_id))

    url_format = "/ci/test-runs?query={query}&index=citest"
    url = DATADOG_BASE_URL + url_format.format(query=quote(query, safe=""))
    return url
