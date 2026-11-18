import warnings

import pytest


def test_not_deprecated():
    import ddtrace

    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")

        assert ddtrace.constants.ENV_KEY
        assert len(ws) == 0


def test_invalid():
    with pytest.raises(ImportError):
        from ddtrace.constants import INVALID_CONSTANT  # noqa:F401
