import pytest

from tests.profiling.utils import with_profiling_test_agent


@pytest.fixture
def profiling_test_agent():
    """Pytest fixture that starts a local profiling capture server for a test.

    Sets _DD_PROFILING_TEST_AGENT_URL so the ddup uploader routes profile uploads
    to the capture server. Yields a _ProfilingCaptureServer scoped to the test.
    """
    with with_profiling_test_agent() as client:
        yield client
