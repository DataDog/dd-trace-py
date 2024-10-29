from ddtrace.debugging._session import _sessions_from_debug_tag
from ddtrace.debugging._session import _sessions_to_debug_tag


def test_tag_identity():
    """
    Test that the combination of _sessions_from_debug_tag and
    _sessions_to_debug_tag is the identity function.
    """
    debug_tag = "session1:1,session2:2"
    assert _sessions_to_debug_tag(_sessions_from_debug_tag(debug_tag)) == debug_tag
