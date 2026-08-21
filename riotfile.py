# type: ignore
import logging
import os

from riot import Venv


logger = logging.getLogger(__name__)
latest = ""

SUPPORTED_PYTHON_VERSIONS: list[tuple[int, int]] = [
    (3, 9),
    (3, 10),
    (3, 11),
    (3, 12),
    (3, 13),
    (3, 14),
]


def version_to_str(version: tuple[int, int]) -> str:
    """Convert a Python version tuple to a string

    >>> version_to_str((3, 9))
    '3.9'
    >>> version_to_str((3, 10))
    '3.10'
    >>> version_to_str((3, 11))
    '3.11'
    >>> version_to_str((3, 12))
    '3.12'
    >>> version_to_str((3, 13))
    '3.13'
    >>> version_to_str((3, 14))
    '3.14'
    >>> version_to_str((3, ))
    '3'
    """
    return ".".join(str(p) for p in version)


def str_to_version(version: str) -> tuple[int, int]:
    """Convert a Python version string to a tuple

    >>> str_to_version("3.9")
    (3, 9)
    >>> str_to_version("3.10")
    (3, 10)
    >>> str_to_version("3.11")
    (3, 11)
    >>> str_to_version("3.12")
    (3, 12)
    >>> str_to_version("3.13")
    (3, 13)
    >>> str_to_version("3.14")
    (3, 14)
    >>> str_to_version("3")
    (3,)
    """
    return tuple(int(p) for p in version.split("."))


MIN_PYTHON_VERSION = version_to_str(min(SUPPORTED_PYTHON_VERSIONS))
MAX_PYTHON_VERSION = version_to_str(max(SUPPORTED_PYTHON_VERSIONS))


def select_pys(min_version: str = MIN_PYTHON_VERSION, max_version: str = MAX_PYTHON_VERSION) -> list[str]:
    """Helper to select python versions from the list of versions we support

    >>> select_pys()
    ['3.9', '3.10', '3.11', '3.12', '3.13', '3.14']
    >>> select_pys(min_version='3')
    ['3.9', '3.10', '3.11', '3.12', '3.13', '3.14']
    >>> select_pys(max_version='3')
    []
    >>> select_pys(min_version='3.9', max_version='3.10')
    ['3.9', '3.10']
    """
    min_version = str_to_version(min_version)
    max_version = str_to_version(max_version)

    return [version_to_str(version) for version in SUPPORTED_PYTHON_VERSIONS if min_version <= version <= max_version]


# NOTE: When NIGHTLY_BUILD is "true" (e.g. in GitLab CI), sets
# DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED for the venv env.
_nightly_build = os.environ.get("NIGHTLY_BUILD") == "true"
_base_env = {
    "_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER": "1",
    "DD_TESTING_RAISE": "1",
    "DD_REMOTE_CONFIGURATION_ENABLED": "false",
    "DD_INJECTION_ENABLED": "1",
    "DD_INJECT_FORCE": "1",
    "DD_PATCH_MODULES": "unittest:false",
    "CMAKE_BUILD_PARALLEL_LEVEL": "12",
    "CARGO_BUILD_JOBS": "12",
    "DD_TRACE_COMPUTE_STATS": "false",
    "DD_CODE_ORIGIN_FOR_SPANS_ENABLED": "false",
    "DD_CIVISIBILITY_BACKEND_API_TIMEOUT_MILLIS": "2000",  # 2-second timeout
    # Enable out-of-session retries for dd-trace-py's own test runs (opt-in feature) so state-leaking flaky tests get a
    # clean-slate retry. Only acts on ATR-exhausted failures. See ddtrace/testing/internal/pytest/plugin.py.
    "_DD_CIVISIBILITY_OUT_OF_SESSION_RETRIES_ENABLED": "1",
}
if _nightly_build:
    _base_env["DD_CIVISIBILITY_CODE_COVERAGE_REPORT_UPLOAD_ENABLED"] = "1"


# Common env configurations for appsec threats testing without/with IAST
_appsec_threats_no_iast_env = {
    "DD_IAST_ENABLED": "false",
    "DD_REMOTE_CONFIGURATION_ENABLED": "false",
    "DD_APPSEC_ENABLED": "true",
}

_appsec_threats_iast_env = {
    "DD_IAST_ENABLED": "true",
    "DD_IAST_REQUEST_SAMPLING": "100",
    "DD_IAST_DEDUPLICATION_ENABLED": "false",
    "DD_IAST_WEAK_HASH_ALGORITHMS": "NOTexist",
    "DD_REMOTE_CONFIGURATION_ENABLED": "false",
    "DD_APPSEC_ENABLED": "true",
}

venv = Venv(
    pkgs={
        "mock": latest,
        "pytest": latest,
        "pytest-mock": latest,
        "coverage": latest,
        "pytest-cov": latest,
        "opentracing": latest,
        "hypothesis": "<6.45.1",
    },
    env=_base_env,
    venvs=[
        Venv(
            name="opentracer",
            pkgs={"opentracing": latest, "pytest-randomly": latest},
            venvs=[
                Venv(
                    pys=select_pys(),
                    command="pytest {cmdargs} tests/opentracer/core",
                ),
                Venv(
                    pys=select_pys(),
                    command="pytest {cmdargs} tests/opentracer/test_tracer_asyncio.py",
                    pkgs={"pytest-asyncio": "==0.21.1"},
                ),
                Venv(
                    command="pytest {cmdargs} tests/opentracer/test_tracer_gevent.py",
                    venvs=[
                        Venv(
                            pys="3.9",
                            pkgs={"gevent": latest, "greenlet": latest},
                        ),
                        Venv(
                            pys="3.10",
                            pkgs={"gevent": latest},
                        ),
                        Venv(
                            pys="3.11",
                            pkgs={"gevent": latest},
                        ),
                        Venv(
                            pys="3.12",
                            pkgs={"gevent": "~=23.9.0"},
                        ),
                        Venv(
                            pys=select_pys(min_version="3.13"),
                            pkgs={"gevent": latest},
                        ),
                    ],
                ),
            ],
        ),
    ],
)
