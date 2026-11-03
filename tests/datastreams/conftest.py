import pytest

from ddtrace.internal.datastreams import current_message


@pytest.fixture(autouse=True)
def _fresh_current_message():
    """Start every test with a clean current-consumed-message context.

    The current-message slot is process-wide thread state shared by classic DSM
    and the pipelines reporter (ddtrace.internal.datastreams.current_message),
    so without this reset a side recorded by an earlier test on the same thread
    leaks into the next test's report (on_produce re-interns the current
    consume side so links stay representable across a window boundary).
    """
    current_message.clear_current_message()
    yield
    current_message.clear_current_message()
