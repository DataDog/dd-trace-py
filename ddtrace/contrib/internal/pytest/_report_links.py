import os
import re
from urllib.parse import quote

from ddtrace import config as ddconfig
from ddtrace.ext import ci
from ddtrace.internal.ci_visibility import CIVisibility


DEFAULT_DATADOG_SITE = "datadoghq.com"
DEFAULT_DATADOG_SUBDOMAIN = "app"

SAFE_FOR_QUERY = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def print_test_report_links(terminalreporter):
    base_url = _get_base_url(
        dd_site=os.getenv("DD_SITE", DEFAULT_DATADOG_SITE), dd_subdomain=os.getenv("DD_SUBDOMAIN", "")
    )
    ci_tags = CIVisibility.get_ci_tags()
    settings = CIVisibility.get_session_settings()
    service = settings.test_service
    env = ddconfig.env

    redirect_test_commit_url = _build_test_commit_redirect_url(base_url, ci_tags, service, env)
    test_runs_url = _build_test_runs_url(base_url, ci_tags)

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


def _get_base_url(dd_site, dd_subdomain):
    # Based on <https://github.com/DataDog/datadog-ci/blob/v3.7.0/master/src/helpers/app.ts>.
    subdomain = dd_subdomain or DEFAULT_DATADOG_SUBDOMAIN
    dd_site_parts = dd_site.split(".")
    if len(dd_site_parts) == 3:
        if subdomain == DEFAULT_DATADOG_SUBDOMAIN:
            return f"https://{dd_site}"
        else:
            return f"https://{subdomain}.{dd_site_parts[1]}.{dd_site_parts[2]}"
    else:
        return f"https://{subdomain}.{dd_site}"


def _build_test_commit_redirect_url(base_url, ci_tags, service, env):
    params = {
        "repo_url": ci_tags.get(ci.git.REPOSITORY_URL),
        "branch": ci_tags.get(ci.git.BRANCH),
        "commit_sha": ci_tags.get(ci.git.COMMIT_SHA),
        "service": service,
    }
    if any(v is None for v in params.values()):
        return None

    url_format = "/ci/redirect/tests/{repo_url}/-/{service}/-/{branch}/-/{commit_sha}"
    url = base_url + url_format.format(**{k: quote(v, safe="") for k, v in params.items()})
    if env:
        url += f"?env={env}".format(env=quote(env, safe=""))

    return url


def _quote_for_query(text):
    if SAFE_FOR_QUERY.match(text):
        return text

    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _build_test_runs_url(base_url, ci_tags):
    ci_job_name = ci_tags.get(ci.JOB_NAME)
    ci_pipeline_id = ci_tags.get(ci.PIPELINE_ID)

    if not (ci_job_name and ci_pipeline_id):
        return None

    query = "@ci.job.name:{} @ci.pipeline.id:{}".format(_quote_for_query(ci_job_name), _quote_for_query(ci_pipeline_id))

    url_format = "/ci/test-runs?query={query}&index=citest"
    url = base_url + url_format.format(query=quote(query, safe=""))
    return url
