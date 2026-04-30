# type: ignore
from riot import Venv

from riotfile import select_pys  # noqa: F401


latest = ""

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
    env={
        "_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER": "1",
        "DD_TESTING_RAISE": "1",
        "DD_REMOTE_CONFIGURATION_ENABLED": "false",
        "DD_INJECTION_ENABLED": "1",
        "DD_INJECT_FORCE": "1",
        "DD_PATCH_MODULES": "unittest:false",
        "CMAKE_BUILD_PARALLEL_LEVEL": "12",
        "CARGO_BUILD_JOBS": "12",
        "DD_PYTEST_USE_NEW_PLUGIN": "true",
        "DD_TRACE_COMPUTE_STATS": "false",
    },
    venvs=[
        # VERSION_SUPPORT_VENVS_START
        # VERSION_SUPPORT_VENVS_END
    ],
)
