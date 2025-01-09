import pytest

from ddtrace.debugging._session import DEFAULT_SESSION_LEVEL
from ddtrace.debugging._session import Session
from ddtrace.debugging._session import _sessions_from_debug_tag
from ddtrace.debugging._session import _sessions_to_debug_tag


@pytest.mark.parametrize(
    "tag,sessions",
    [
        ("session1", [Session("session1", DEFAULT_SESSION_LEVEL)]),
        ("session1:2", [Session("session1", 2)]),
        ("session1:1.session2:2", [Session("session1", 1), Session("session2", 2)]),
        ("session1.session2", [Session("session1", DEFAULT_SESSION_LEVEL), Session("session2", DEFAULT_SESSION_LEVEL)]),
    ],
)
def test_session_parse(tag, sessions):
    assert list(_sessions_from_debug_tag(tag)) == sessions


@pytest.mark.parametrize(
    "a,b",
    [
        ("session1:1.session2:2", "session1.session2:2"),
        ("session1:0.session2:2", "session1:0.session2:2"),
        ("session1.session2", "session1.session2"),
    ],
)
def test_tag_identity(a, b):
    """
    Test that the combination of _sessions_from_debug_tag and
    _sessions_to_debug_tag is the identity function.
    """
    assert _sessions_to_debug_tag(_sessions_from_debug_tag(a)) == b
