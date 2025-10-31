import pytest

from tests.appsec.integrations.utils_testagent import clear_session
from tests.appsec.integrations.utils_testagent import start_trace


@pytest.fixture
def iast_test_token(request):
    """Provide a test-agent session token based on the current test name.

    The fixture starts a tracing session for the test using the pytest node name
    as the token, yields it to the test, and ensures the session is cleared
    afterwards. This centralizes the repeated start/clear logic used by
    testagent-based IAST tests.
    """
    token = request.node.name
    _ = start_trace(token)
    try:
        yield token
    finally:
        clear_session(token)
