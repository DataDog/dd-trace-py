from collections import defaultdict
from doctest import DocTest
from itertools import count
from itertools import groupby
import json
import os
import sys
from typing import Dict
from typing import Iterable
from typing import List
from typing import Tuple
import warnings

import pytest

import ddtrace
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.pytest.constants import FRAMEWORK
from ddtrace.contrib.pytest.constants import HELP_MSG
from ddtrace.contrib.pytest.constants import KIND
from ddtrace.contrib.trace_utils import int_service
from ddtrace.ext import SpanTypes
from ddtrace.ext import ci
from ddtrace.ext import test
from ddtrace.filters import TraceCiVisibilityFilter
from ddtrace.internal import compat
from ddtrace.internal.logger import get_logger
from ddtrace.pin import Pin


PATCH_ALL_HELP_MSG = "Call ddtrace.patch_all before running tests."
log = get_logger(__name__)


def is_enabled(config):
    """Check if the ddtrace plugin is enabled."""
    return config.getoption("ddtrace") or config.getini("ddtrace")


def _extract_span(item):
    """Extract span from `pytest.Item` instance."""
    return getattr(item, "_datadog_span", None)


def _store_span(item, span):
    """Store span at `pytest.Item` instance."""
    setattr(item, "_datadog_span", span)


def _extract_repository_name(repository_url):
    # type: (str) -> str
    """Extract repository name from repository url."""
    try:
        return compat.parse.urlparse(repository_url).path.rstrip(".git").rpartition("/")[-1]
    except ValueError:
        # In case of parsing error, default to repository url
        log.warning("Repository name cannot be parsed from repository_url: %s", repository_url)
        return repository_url


def pytest_addoption(parser):
    """Add ddtrace options."""
    group = parser.getgroup("ddtrace")

    group._addoption(
        "--ddtrace",
        action="store_true",
        dest="ddtrace",
        default=False,
        help=HELP_MSG,
    )

    group._addoption(
        "--ddtrace-patch-all",
        action="store_true",
        dest="ddtrace-patch-all",
        default=False,
        help=PATCH_ALL_HELP_MSG,
    )

    parser.addini("ddtrace", HELP_MSG, type="bool")
    parser.addini("ddtrace-patch-all", PATCH_ALL_HELP_MSG, type="bool")


def pytest_configure(config):
    config.addinivalue_line("markers", "dd_tags(**kwargs): add tags to current span")
    if is_enabled(config):
        ci_tags = ci.tags()
        if ci_tags.get(ci.git.REPOSITORY_URL, None) and int_service(None, ddtrace.config.pytest) == "pytest":
            repository_name = _extract_repository_name(ci_tags[ci.git.REPOSITORY_URL])
            ddtrace.config.pytest["service"] = repository_name
        Pin(tags=ci_tags, _config=ddtrace.config.pytest).onto(config)


def pytest_sessionstart(session):
    pin = Pin.get_from(session.config)
    if pin is not None:
        tracer_filters = pin.tracer._filters
        if not any(isinstance(tracer_filter, TraceCiVisibilityFilter) for tracer_filter in tracer_filters):
            tracer_filters += [TraceCiVisibilityFilter()]
            pin.tracer.configure(settings={"FILTERS": tracer_filters})

        if os.environ.get("DD_CIVISIBILITY_CODE_COVERAGE_ENABLED") == "true":
            if session.config.pluginmanager.hasplugin("_cov"):
                plugin = session.config.pluginmanager.getplugin("_cov")
                if plugin.options.cov_context:
                    session.config.pluginmanager.unregister(name="_cov_contexts")
                    warnings.warn(
                        pytest.PytestWarning("_cov_contexts plugin is not compatible with ddtrace and will be disabled")
                    )
                    # raise RuntimeError(
                    #     "DD_CIVISIBILITY_CODE_COVERAGE_ENABLED=true and --cov-context=test are incompatible. "
                    #     "Remove --cov-context option to enable code coverage reporting in DataDog."
                    # )
                if plugin.cov_controller:
                    cov = plugin.cov_controller.cov
                    session.config.pluginmanager.register(CoveragePlugin(cov), "_datadog_cov_contexts")


def pytest_sessionfinish(session, exitstatus):
    """Flush open tracer."""
    pin = Pin.get_from(session.config)
    if pin is not None:

        # TODO enable code coverage submission
        # if os.environ.get("DD_CIVISIBILITY_CODE_COVERAGE_ENABLED") == "true":
        #     if session.config.pluginmanager.hasplugin("_cov"):
        #         plugin = session.config.pluginmanager.getplugin("_cov")
        #        if plugin.cov_controller:
        #             cov = plugin.cov_controller.cov
        #             for coverage in CIVisibilityReporter(cov).yield_per_context():
        #                 requests.post(
        #                     "https://ci.datadoghq.com/api/v1/ci/coverage",
        #                     headers={"Content-Type": "application/json", "dd-api-key": os.environ["DD_API_KEY"]},
        #                     data=json.dumps(coverage),
        #                 )
        #                 # proposed API: pin.tracer._ci.coverage.report(coverage)

        pin.tracer.shutdown()


@pytest.fixture(scope="function")
def ddspan(request):
    pin = Pin.get_from(request.config)
    if pin:
        return _extract_span(request.node)


@pytest.fixture(scope="session", autouse=True)
def patch_all(request):
    if request.config.getoption("ddtrace-patch-all") or request.config.getini("ddtrace-patch-all"):
        ddtrace.patch_all()


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    pin = Pin.get_from(item.config)
    if pin is None:
        yield
        return
    with pin.tracer.trace(
        ddtrace.config.pytest.operation_name,
        service=int_service(pin, ddtrace.config.pytest),
        resource=item.nodeid,
        span_type=SpanTypes.TEST,
    ) as span:
        span.context.dd_origin = ci.CI_APP_TEST_ORIGIN
        span.context.sampling_priority = AUTO_KEEP
        span.set_tags(pin.tags)
        span.set_tag(SPAN_KIND, KIND)
        span.set_tag(test.FRAMEWORK, FRAMEWORK)
        span.set_tag(test.NAME, item.name)
        if hasattr(item, "module"):
            span.set_tag(test.SUITE, item.module.__name__)
        elif hasattr(item, "dtest") and isinstance(item.dtest, DocTest):
            span.set_tag(test.SUITE, item.dtest.globs["__name__"])
        span.set_tag(test.TYPE, SpanTypes.TEST)
        span.set_tag(test.FRAMEWORK_VERSION, pytest.__version__)

        # We preemptively set FAIL as a status, because if pytest_runtest_makereport is not called
        # (where the actual test status is set), it means there was a pytest error
        span.set_tag(test.STATUS, test.Status.FAIL.value)

        # Parameterized test cases will have a `callspec` attribute attached to the pytest Item object.
        # Pytest docs: https://docs.pytest.org/en/6.2.x/reference.html#pytest.Function
        if getattr(item, "callspec", None):
            parameters = {"arguments": {}, "metadata": {}}  # type: Dict[str, Dict[str, str]]
            for param_name, param_val in item.callspec.params.items():
                try:
                    parameters["arguments"][param_name] = repr(param_val)
                except Exception:
                    parameters["arguments"][param_name] = "Could not encode"
                    log.warning("Failed to encode %r", param_name, exc_info=True)
            span.set_tag(test.PARAMETERS, json.dumps(parameters))

        markers = [marker.kwargs for marker in item.iter_markers(name="dd_tags")]
        for tags in markers:
            span.set_tags(tags)
        _store_span(item, span)

        yield


def _extract_reason(call):
    if call.excinfo is not None:
        return call.excinfo.value


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store outcome for tracing."""
    outcome = yield

    span = _extract_span(item)
    if span is None:
        return

    is_setup_or_teardown = call.when == "setup" or call.when == "teardown"
    has_exception = call.excinfo is not None

    if is_setup_or_teardown and not has_exception:
        return

    result = outcome.get_result()
    xfail = hasattr(result, "wasxfail") or "xfail" in result.keywords
    has_skip_keyword = any(x in result.keywords for x in ["skip", "skipif", "skipped"])

    # If run with --runxfail flag, tests behave as if they were not marked with xfail,
    # that's why no test.XFAIL_REASON or test.RESULT tags will be added.
    if result.skipped:
        if xfail and not has_skip_keyword:
            # XFail tests that fail are recorded skipped by pytest, should be passed instead
            span.set_tag(test.STATUS, test.Status.PASS.value)
            if not item.config.option.runxfail:
                span.set_tag(test.RESULT, test.Status.XFAIL.value)
                span.set_tag(test.XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
        else:
            span.set_tag(test.STATUS, test.Status.SKIP.value)
        reason = _extract_reason(call)
        if reason is not None:
            span.set_tag(test.SKIP_REASON, reason)
    elif result.passed:
        span.set_tag(test.STATUS, test.Status.PASS.value)
        if xfail and not has_skip_keyword and not item.config.option.runxfail:
            # XPass (strict=False) are recorded passed by pytest
            span.set_tag(test.XFAIL_REASON, getattr(result, "wasxfail", "XFail"))
            span.set_tag(test.RESULT, test.Status.XPASS.value)
    else:
        span.set_tag(test.STATUS, test.Status.FAIL.value)
        if xfail and not has_skip_keyword and not item.config.option.runxfail:
            # XPass (strict=True) are recorded failed by pytest, longrepr contains reason
            span.set_tag(test.XFAIL_REASON, getattr(result, "longrepr", "XFail"))
            span.set_tag(test.RESULT, test.Status.XPASS.value)
        if call.excinfo:
            span.set_exc_info(call.excinfo.type, call.excinfo.value, call.excinfo.tb)

    if False and os.environ.get("DD_CIVISIBILITY_CODE_COVERAGE_ENABLED") == "true":
        # NOTE enable in case we would like to submit coverage data as tags
        if item.config.pluginmanager.hasplugin("_cov"):
            plugin = item.config.pluginmanager.getplugin("_cov")
            if plugin.cov_controller:
                cov = plugin.cov_controller.cov
                pytest.set_trace()
                coverage = CIVisibilityReporter(cov).build(test_id=str(span.trace_id))
                span.set_tag("test.coverage", json.dumps(coverage[:2]))


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Add test coverage report to terminal summary."""
    if os.environ.get("DD_CIVISIBILITY_CODE_COVERAGE_ENABLED") == "true":
        if config.pluginmanager.hasplugin("_cov"):
            plugin = config.pluginmanager.getplugin("_cov")
            if plugin.cov_controller:
                cov = plugin.cov_controller.cov
                with open(os.path.join(config.rootdir, ".coverage-ci.json"), "w") as f:
                    for coverage in CIVisibilityReporter(cov).yield_per_context():
                        line = json.dumps(coverage, indent=None)
                        if config.option.verbose > 2:
                            terminalreporter.write_line(line)
                        f.write(line)
                # alternative way to submit coverage data
                with open(os.path.join(config.rootdir, ".coverage-ci-v2.json"), "w") as f:
                    f.write(json.dumps(CIVisibilityReporter(cov).yield_per_context_v2(), indent=None))


class CoveragePlugin:
    def __init__(self, cov):
        self.cov = cov

    def pytest_runtest_setup(self, item):
        self.switch_context(item, "setup")

    def pytest_runtest_teardown(self, item):
        self.switch_context(item, "teardown")

    def pytest_runtest_call(self, item):
        self.switch_context(item, "run")

    def switch_context(self, item, when):
        context = "{item.nodeid}|{when}".format(item=item, when=when)
        span = _extract_span(item)
        if span is not None:
            context = str(span.trace_id)
        self.cov.switch_context(context)
        os.environ["COV_CORE_CONTEXT"] = context


class CIVisibilityReporter:
    """A reporter for writing CI Visibility JSON coverage results."""

    report_type = "CI Visibility JSON report"

    def __init__(self, coverage):
        self.coverage = coverage
        self.config = self.coverage.config

    def segments(self, lines):
        """Extract the relevant report data for a single file."""

        def as_segments(it):
            # type: (Iterable[int]) -> Tuple[int, int, int, int, int]
            sequence = list(it)  # type: List[int]
            return (sequence[0], 0, sequence[-1], 0, -1)

        executed = sorted(lines)
        return [as_segments(g) for _, g in groupby(executed, lambda n, c=count(): n - next(c))]

    def yield_per_context(self, morfs=None):
        """Yield the coverage report for each available context."""
        # lazy import to avoid a hard dependency
        from coverage.report import get_analysis_to_report

        # test_id -> filename -> segments
        tests = defaultdict(lambda: defaultdict(list))

        for file_reporter, analysis in get_analysis_to_report(self.coverage, morfs):
            if not analysis.executed:
                continue

            filename = file_reporter.relative_filename()
            for line, contexts in analysis.data.contexts_by_lineno(analysis.filename).items():
                line = int(line)
                for context in contexts:
                    tests[context][filename].append(line)

        for test_id, stats in tests.items():
            yield {
                "type": "coverage",
                "version": 1,
                "content": {
                    "test_id": test_id,
                    "files": [
                        {"filename": filename, "segments": self.segments(lines)} for filename, lines in stats.items()
                    ],
                },
            }

    def yield_per_context_v2(self, morfs=None):
        """Yield the coverage report for each available context."""
        # lazy import to avoid a hard dependency
        from coverage.report import get_analysis_to_report

        # filename -> line number -> test_ids

        def segments(lines_with_context):
            def as_segments(it):
                sequence = list(it)
                if len(sequence) == 1:
                    return (str(sequence[0][0]), sequence[0][1])
                return (str(sequence[0][0]) + "-" + str(sequence[-1][0]), sequence[0][1])

            executed = sorted(lines_with_context)
            return dict(as_segments(g) for _, g in groupby(executed, lambda n, c=count(): (n[0] - next(c), n[1])))

        return {
            f: r
            for f, r in (
                (
                    file_reporter.relative_filename(),
                    segments(
                        (int(line), sorted(test_ids))
                        for line, test_ids in analysis.data.contexts_by_lineno(analysis.filename).items()
                        if test_ids and test_ids != [""]
                    ),
                )
                for file_reporter, analysis in get_analysis_to_report(self.coverage, morfs)
                if analysis.executed
            )
            if r
        }

    def build(self, morfs=None, test_id=None):
        """Generate a CI Visibility structure.

        `morfs` is a list of modules or file names.
        """
        # lazy import to avoid a hard dependency
        from coverage.report import get_analysis_to_report

        coverage_data = self.coverage.get_data()
        coverage_data.set_query_contexts([test_id])
        measured_files = []

        for file_reporter, analysis in get_analysis_to_report(self.coverage, morfs):
            if not analysis.executed:
                continue
            measured_files.append(
                {
                    "filename": file_reporter.relative_filename(),
                    "segments": self.segments(list(analysis.executed)),
                }
            )
        return measured_files

    def report(self, morfs=None, outfile=None, test_id=None):
        """Generate a CI Visibility report.

        `morfs` is a list of modules or file names.
        `outfile` is a file object to write the json to.
        """
        outfile = outfile or sys.stdout

        json.dump(
            {
                "type": "coverage",
                "version": 1,
                "content": {
                    "test_id": test_id,
                    "files": self.build(morfs=morfs, test_id=test_id),
                },
            },
            outfile,
            indent=(4 if self.config.json_pretty_print else None),
        )
