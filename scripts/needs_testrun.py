#!/usr/bin/env python3

from argparse import ArgumentParser
import fnmatch
import json
import logging
import os
from pathlib import Path
import re
from subprocess import check_output
import sys
import tempfile
import typing as t
from urllib.request import Request
from urllib.request import urlopen


logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

LOGGER = logging.getLogger(__name__)

BASE_BRANCH_PATTERN = re.compile(r':<span class="css-truncate-target">([^<]+)')
SUITESPECFILE = Path(__file__).parents[1] / "tests" / ".suitespec.json"

SUITES = (
    "aiobotocore",
    "aiohttp",
    "aiomysql",
    "aiopg",
    "aioredis",
    "asyncpg",
    "algoliasearch",
    "asgi",
    "aws_lambda",
    "boto",
    "bottle",
    "cassandra",
    "celery",
    "cherrypy",
    "ci_visibility",
    "consul",
    "datastreams",
    "ddtracerun",
    "debugger",
    "dogpile_cache",
    "django",
    "django_hosts",
    "djangorestframework",
    "elasticsearch",
    "falcon",
    "fastapi",
    "flask",
    "gevent",
    "graphql",
    "graphene",
    "grpc",
    "gunicorn",
    "httplib",
    "httpx",
    "internal",
    "integration_agent",
    "integration_testagent",
    "vendor",
    "profile",
    "jinja2",
    "kafka",
    "kombu",
    "langchain",
    "mako",
    "mariadb",
    "molten",
    "mongoengine",
    "mysqlconnector",
    "mysqlpython",
    "openai",
    "opentracer",
    "opentelemetry",
    "psycopg",
    "pylibmc",
    "pylons",
    "pymemcache",
    "pymongo",
    "pymysql",
    "pynamodb",
    "pyodbc",
    "pyramid",
    "pytest",
    "asynctest",
    "pytestbdd",
    "aredis",
    "yaaredis",
    "redis",
    "rediscluster",
    "requests",
    "rq",
    "sanic",
    "snowflake",
    "sqlalchemy",
    "sourcecode",
    "starlette",
    "stdlib",
    "test_logging",
    "tracer",
    "telemetry",
    "appsec",
    "tornado",
    "urllib3",
    "vertica",
    "wsgi",
)


def get_base_branch(pr_number: int) -> str:
    """Get the base branch of a PR

    >>> get_base_branch(6412)
    '1.x'
    """

    pr_page_content = urlopen(f"https://github.com/DataDog/dd-trace-py/pull/{pr_number}").read().decode("utf-8")

    return BASE_BRANCH_PATTERN.search(pr_page_content).group(1)


def get_changed_files(pr_number: int) -> t.Set[str]:
    """Get the files changed in a PR

    >>> sorted(get_changed_files(6388))  # doctest: +NORMALIZE_WHITESPACE
    ['ddtrace/debugging/_expressions.py',
    'releasenotes/notes/fix-debugger-expressions-none-literal-30f3328d2e386f40.yaml',
    'tests/debugging/test_expressions.py']
    """
    try:
        # Try with the GitHub REST API for the most accurate result
        url = f"https://api.github.com/repos/datadog/dd-trace-py/pulls/{pr_number}/files"
        headers = {"Accept": "application/vnd.github+json"}

        return {_["filename"] for _ in json.load(urlopen(Request(url, headers=headers)))}

    except Exception:
        # If that fails use the less accurate method of diffing against the base
        # branch
        LOGGER.warning("Failed to get changed files from GitHub API, using git diff instead")
        return set(
            check_output(
                [
                    "git",
                    "diff",
                    "--name-only",
                    "HEAD",
                    get_base_branch(),
                ]
            )
            .decode("utf-8")
            .strip()
            .splitlines()
        )


def get_patterns(suite: str) -> t.Set[str]:
    """Get the patterns for a suite

    >>> sorted(get_patterns("debugger"))  # doctest: +NORMALIZE_WHITESPACE
    ['ddtrace/__init__.py', 'ddtrace/_hooks.py', 'ddtrace/_logger.py', 'ddtrace/_monkey.py', 'ddtrace/auto.py',
    'ddtrace/bootstrap/*', 'ddtrace/commands/*', 'ddtrace/constants.py', 'ddtrace/context.py',
    'ddtrace/debugging/*', 'ddtrace/filter.py', 'ddtrace/internal/*', 'ddtrace/pin.py', 'ddtrace/provider.py',
    'ddtrace/sampler.py', 'ddtrace/settings/__init__.py', 'ddtrace/settings/config.py',
    'ddtrace/settings/dynamic_instrumentation.py', 'ddtrace/settings/exception_debugging.py',
    'ddtrace/settings/http.py', 'ddtrace/settings/integration.py', 'ddtrace/span.py', 'ddtrace/tracer.py',
    'tests/commands/*', 'tests/debugging/*', 'tests/integration/*', 'tests/internal/*', 'tests/lib-injection',
    'tests/tracer/*']
    >>> get_patterns("foobar")
    set()
    """
    with SUITESPECFILE.open() as f:
        suitespec = json.load(f)

        compos = suitespec["components"]
        suite_patterns = set(suitespec["suites"].get(suite, []))

        def resolve(patterns: set) -> set:
            refs = {_ for _ in patterns if _.startswith("@")}
            resolved_patterns = patterns - refs

            # Recursively resolve references
            for ref in refs:
                try:
                    resolved_patterns |= resolve(set(compos[ref[1:]]))
                except KeyError:
                    raise ValueError(f"Unknown component reference: {ref}")

            return resolved_patterns

        return resolve(suite_patterns)


def needs_testrun(suite: str, pr_number: int) -> bool:
    """Check if a testrun is needed for a suite and PR

    >>> needs_testrun("debugger", 6412)
    False
    >>> needs_testrun("debugger", 6388)
    True
    >>> needs_testrun("foobar", 6412)
    True
    """
    try:
        patterns = get_patterns(suite)
    except Exception:
        LOGGER.error("Failed to get patterns")
        return True
    if not patterns:
        # We don't have patterns so we run the tests
        LOGGER.info("No patterns for suite '%s', running all tests", suite)
        return True

    try:
        changed_files = get_changed_files(pr_number)
    except Exception:
        LOGGER.error("Failed to get changed files")
        return True
    if not changed_files:
        # No files changed, no need to run the tests
        LOGGER.info("No files changed, not running tests")
        return False

    matches = [_ for p in patterns for _ in fnmatch.filter(changed_files, p)]

    LOGGER.info("Changed files:")
    for f in changed_files:
        LOGGER.info("  %s", f)
    LOGGER.info("Patterns for suite '%s':", suite)
    for p in patterns:
        LOGGER.info("  %s", p)
    if matches:
        LOGGER.info("Changed files matching patterns:")
        for m in matches:
            LOGGER.info("  %s", m)
    else:
        LOGGER.info("No changed files match patterns")

    return bool(matches)


def _get_pr_number():
    number = os.environ.get("CIRCLE_PR_NUMBER")
    if not number:
        pr_url = os.environ.get("CIRCLE_PULL_REQUEST", "")
        number = pr_url.split("/")[-1]
    try:
        return int(number)
    except ValueError:
        return 0


def for_each_testrun_needed(action: t.Callable[[str], None], cached: bool = True):
    # Used in CircleCI config
    tempdir = Path(tempfile.gettempdir())
    pr_number = _get_pr_number()

    for suite in SUITES:
        if pr_number <= 0:
            # If we don't have a valid PR number we run all tests
            action(suite)
            continue

        cachefile = tempdir / f"needs-testrun-{suite}-{pr_number}"
        if cached and cachefile.exists():
            # If we cached the result of the previous run we can skip the check
            if int(cachefile.read_text().strip()):
                action(suite)
            continue

        needs_run = needs_testrun(suite, pr_number)
        if needs_run:
            action(suite)

        if cached:
            # Cache the result of the check to save on API calls
            cachefile.write_text(str(int(needs_run)))


def main() -> bool:
    argp = ArgumentParser()

    argp.add_argument("suite", help="The suite to use", type=str)
    argp.add_argument("pr", help="The PR number", type=int)
    argp.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = argp.parse_args()

    if args.verbose:
        LOGGER.setLevel(logging.INFO)

    return needs_testrun(args.suite, args.pr)


if __name__ == "__main__":
    sys.exit(not main())
