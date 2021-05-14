import warnings

import pytest


def test_deprecated_public_constants():
    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")

        from ddtrace.constants import ENV_KEY
        from ddtrace.constants import MANUAL_DROP_KEY

        assert ENV_KEY and MANUAL_DROP_KEY

        (w,) = ws
        assert issubclass(w.category, DeprecationWarning)
        assert "constant ENV_KEY has been deprecated and will be removed in v1.0" == str(w.message)


def test_invalid_constant():
    with pytest.raises(ImportError):
        from ddtrace.constants import INVALID_CONSTANT  # noqa
