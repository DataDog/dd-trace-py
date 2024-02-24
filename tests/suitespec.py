from functools import cache
import json
from pathlib import Path
import typing as t


SUITESPECFILE = Path(__file__).parents[1] / "tests" / ".suitespec.json"
SUITESPEC = json.loads(SUITESPECFILE.read_text())


@cache
def get_patterns(suite: str) -> t.Set[str]:
    """Get the patterns for a suite

    >>> sorted(get_patterns("debugger"))  # doctest: +NORMALIZE_WHITESPACE
    ['.circleci/*', 'ddtrace/__init__.py', 'ddtrace/_hooks.py', 'ddtrace/_logger.py', 'ddtrace/_monkey.py',
    'ddtrace/_trace/*', 'ddtrace/auto.py', 'ddtrace/bootstrap/*', 'ddtrace/commands/*', 'ddtrace/constants.py',
    'ddtrace/context.py', 'ddtrace/debugging/*', 'ddtrace/filters.py', 'ddtrace/internal/__init__.py',
    'ddtrace/internal/_encoding.py*', 'ddtrace/internal/_rand.pyi', 'ddtrace/internal/_rand.pyx',
    'ddtrace/internal/_stdint.h', 'ddtrace/internal/_tagset.py*', 'ddtrace/internal/_utils.*',
    'ddtrace/internal/agent.py', 'ddtrace/internal/assembly.py', 'ddtrace/internal/atexit.py',
    'ddtrace/internal/compat.py', 'ddtrace/internal/constants.py', 'ddtrace/internal/core/*',
    'ddtrace/internal/datadog/__init__.py', 'ddtrace/internal/debug.py', 'ddtrace/internal/dogstatsd.py',
    'ddtrace/internal/encoding.py', 'ddtrace/internal/forksafe.py', 'ddtrace/internal/gitmetadata.py',
    'ddtrace/internal/glob_matching.py', 'ddtrace/internal/hostname.py', 'ddtrace/internal/http.py',
    'ddtrace/internal/injection.py', 'ddtrace/internal/logger.py', 'ddtrace/internal/metrics.py',
    'ddtrace/internal/module.py', 'ddtrace/internal/pack.h', 'ddtrace/internal/pack_template.h',
    'ddtrace/internal/packages.py', 'ddtrace/internal/peer_service/*', 'ddtrace/internal/periodic.py',
    'ddtrace/internal/processor/__init__.py', 'ddtrace/internal/processor/stats.py',
    'ddtrace/internal/rate_limiter.py', 'ddtrace/internal/remoteconfig/*', 'ddtrace/internal/runtime/*',
    'ddtrace/internal/safety.py', 'ddtrace/internal/sampling.py', 'ddtrace/internal/schema/*',
    'ddtrace/internal/service.py', 'ddtrace/internal/sma.py', 'ddtrace/internal/sysdep.h',
    'ddtrace/internal/tracemethods.py', 'ddtrace/internal/uds.py', 'ddtrace/internal/utils/*',
    'ddtrace/internal/uwsgi.py', 'ddtrace/internal/wrapping/*', 'ddtrace/internal/writer/*', 'ddtrace/pin.py',
    'ddtrace/provider.py', 'ddtrace/py.typed', 'ddtrace/sampler.py', 'ddtrace/sampling_rule.py',
    'ddtrace/settings/__init__.py', 'ddtrace/settings/config.py', 'ddtrace/settings/dynamic_instrumentation.py',
    'ddtrace/settings/exception_debugging.py', 'ddtrace/settings/exceptions.py', 'ddtrace/settings/http.py',
    'ddtrace/settings/integration.py', 'ddtrace/settings/peer_service.py', 'ddtrace/span.py', 'ddtrace/tracer.py',
    'ddtrace/tracing/*', 'ddtrace/version.py', 'docker/*', 'hatch.toml', 'pyproject.toml', 'riotfile.py',
    'scripts/ddtest', 'scripts/run-test-suite', 'setup.cfg', 'setup.py', 'tests/.suitespec.json', 'tests/__init__.py',
    'tests/conftest.py', 'tests/debugging/*', 'tests/lib-injection/dd-lib-python-init-test-django-gunicorn-alpine/*',
    'tests/lib-injection/dd-lib-python-init-test-django-gunicorn/*',
    'tests/lib-injection/dd-lib-python-init-test-django-no-perms/*',
    'tests/lib-injection/dd-lib-python-init-test-django-pre-installed/*',
    'tests/lib-injection/dd-lib-python-init-test-django-unsupported-python/*',
    'tests/lib-injection/dd-lib-python-init-test-django-uvicorn/*',
    'tests/lib-injection/dd-lib-python-init-test-django/*', 'tests/meta/*', 'tests/smoke_test.py',
    'tests/subprocesstest.py', 'tests/suitespec.py', 'tests/test_module/*', 'tests/utils.py',
    'tests/wait-for-services.py', 'tests/webclient.py']
    >>> get_patterns("foobar")
    set()
    >>> sorted(get_patterns("urllib3"))  # doctest: +NORMALIZE_WHITESPACE
    ['.circleci/*', 'ddtrace/__init__.py', 'ddtrace/_hooks.py', 'ddtrace/_logger.py', 'ddtrace/_monkey.py',
    'ddtrace/_trace/*', 'ddtrace/auto.py', 'ddtrace/bootstrap/*', 'ddtrace/commands/*', 'ddtrace/constants.py',
    'ddtrace/context.py', 'ddtrace/contrib/__init__.py', 'ddtrace/contrib/trace_utils.py',
    'ddtrace/contrib/trace_utils_async.py', 'ddtrace/contrib/urllib3/*', 'ddtrace/ext/__init__.py',
    'ddtrace/ext/http.py', 'ddtrace/ext/net.py', 'ddtrace/ext/sql.py', 'ddtrace/ext/test.py', 'ddtrace/ext/user.py',
    'ddtrace/filters.py', 'ddtrace/internal/__init__.py', 'ddtrace/internal/_encoding.py*',
    'ddtrace/internal/_rand.pyi', 'ddtrace/internal/_rand.pyx', 'ddtrace/internal/_stdint.h',
    'ddtrace/internal/_tagset.py*', 'ddtrace/internal/_utils.*', 'ddtrace/internal/agent.py',
    'ddtrace/internal/assembly.py', 'ddtrace/internal/atexit.py', 'ddtrace/internal/compat.py',
    'ddtrace/internal/constants.py', 'ddtrace/internal/core/*', 'ddtrace/internal/datadog/__init__.py',
    'ddtrace/internal/debug.py', 'ddtrace/internal/dogstatsd.py', 'ddtrace/internal/encoding.py',
    'ddtrace/internal/forksafe.py', 'ddtrace/internal/gitmetadata.py', 'ddtrace/internal/glob_matching.py',
    'ddtrace/internal/hostname.py', 'ddtrace/internal/http.py', 'ddtrace/internal/injection.py',
    'ddtrace/internal/logger.py', 'ddtrace/internal/metrics.py', 'ddtrace/internal/module.py',
    'ddtrace/internal/pack.h', 'ddtrace/internal/pack_template.h', 'ddtrace/internal/packages.py',
    'ddtrace/internal/peer_service/*', 'ddtrace/internal/periodic.py', 'ddtrace/internal/processor/__init__.py',
    'ddtrace/internal/processor/stats.py', 'ddtrace/internal/rate_limiter.py', 'ddtrace/internal/runtime/*',
    'ddtrace/internal/safety.py', 'ddtrace/internal/sampling.py', 'ddtrace/internal/schema/*',
    'ddtrace/internal/service.py', 'ddtrace/internal/sma.py', 'ddtrace/internal/sysdep.h',
    'ddtrace/internal/tracemethods.py', 'ddtrace/internal/uds.py', 'ddtrace/internal/utils/*',
    'ddtrace/internal/uwsgi.py', 'ddtrace/internal/wrapping/*', 'ddtrace/internal/writer/*', 'ddtrace/pin.py',
    'ddtrace/propagation/*', 'ddtrace/provider.py', 'ddtrace/py.typed', 'ddtrace/sampler.py',
    'ddtrace/sampling_rule.py', 'ddtrace/settings/__init__.py', 'ddtrace/settings/_database_monitoring.py',
    'ddtrace/settings/config.py', 'ddtrace/settings/exceptions.py', 'ddtrace/settings/http.py',
    'ddtrace/settings/integration.py', 'ddtrace/settings/peer_service.py', 'ddtrace/span.py', 'ddtrace/tracer.py',
    'ddtrace/tracing/*', 'ddtrace/version.py', 'docker/*', 'hatch.toml', 'pyproject.toml', 'riotfile.py',
    'scripts/ddtest', 'scripts/run-test-suite', 'setup.cfg', 'setup.py', 'tests/.suitespec.json', 'tests/__init__.py',
    'tests/conftest.py', 'tests/contrib/__init__.py', 'tests/contrib/config.py', 'tests/contrib/patch.py',
    'tests/contrib/urllib3/*', 'tests/lib-injection/dd-lib-python-init-test-django-gunicorn-alpine/*',
    'tests/lib-injection/dd-lib-python-init-test-django-gunicorn/*',
    'tests/lib-injection/dd-lib-python-init-test-django-no-perms/*',
    'tests/lib-injection/dd-lib-python-init-test-django-pre-installed/*',
    'tests/lib-injection/dd-lib-python-init-test-django-unsupported-python/*',
    'tests/lib-injection/dd-lib-python-init-test-django-uvicorn/*',
    'tests/lib-injection/dd-lib-python-init-test-django/*', 'tests/meta/*', 'tests/smoke_test.py',
    'tests/snapshots/tests.contrib.urllib3.*', 'tests/subprocesstest.py', 'tests/suitespec.py', 'tests/test_module/*',
    'tests/utils.py', 'tests/wait-for-services.py', 'tests/webclient.py']
    """
    compos = SUITESPEC["components"]
    if suite not in SUITESPEC["suites"]:
        return set()

    suite_patterns = set(SUITESPEC["suites"][suite])

    # Include patterns from include-always components
    for patterns in (patterns for compo, patterns in compos.items() if compo.startswith("$")):
        suite_patterns |= set(patterns)

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

    return {_.format(suite=suite) for _ in resolve(suite_patterns)}


def get_suites() -> t.Set[str]:
    """Get the list of suites."""
    return set(SUITESPEC["suites"].keys())
