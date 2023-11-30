import warnings

import pytest


def test_not_deprecated():
    import ddtrace.constants

    with warnings.catch_warnings(record=True) as warns:
        warnings.simplefilter("always")
        assert ddtrace.constants.IAST_ENV
        assert ddtrace.constants.APPSEC_ENV
        assert len(warns) == 0


def test_invalid():
    with pytest.raises(ImportError):
        from ddtrace.constants import INVALID_CONSTANT  # noqa:F401
