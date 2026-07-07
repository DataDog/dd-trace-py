import pytest

from tests.profiling.utils import with_profiling_test_agent


@pytest.fixture(autouse=True)
def profiling_test_agent():
    """Start a self-contained mini agent for every profiling test.

    Autouse so that subprocess tests automatically inherit _DD_PROFILING_TEST_TOKEN
    and _DD_PROFILING_TEST_PROXY_URL without needing to call with_profiling_test_agent()
    inside the subprocess function body.  The yielded _ProfilingMiniAgentClient is
    available to non-subprocess tests that declare profiling_test_agent as a fixture
    parameter.
    """
    with with_profiling_test_agent() as client:
        yield client
