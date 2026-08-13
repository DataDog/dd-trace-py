import pytest

from ddtrace.appsec._iast._taint_tracking._native import aspects


def test_native_join_aspect_rejects_invalid_separator():
    with pytest.raises(TypeError, match="join separator must be str, bytes, or bytearray"):
        aspects.join_aspect(None, ["value"])
