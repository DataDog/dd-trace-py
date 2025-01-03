import pytest

from ddtrace.debugging._session import _sessions_from_debug_tag
from ddtrace.debugging._session import _sessions_to_debug_tag


@pytest.mark.parametrize(
    "a,b",
    [
        ("session1:1.session2:2", "session1.session2:2"),
        ("session1:0.session2:2", "session1:0.session2:2"),
    ],
)
def test_tag_identity(a, b):
    """
    Test that the combination of _sessions_from_debug_tag and
    _sessions_to_debug_tag is the identity function.
    """
    assert _sessions_to_debug_tag(_sessions_from_debug_tag(a)) == b
