import sys
import warnings


def test_deprecated_public_constants():
    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")

        from ddtrace.constants import ENV_KEY
        from ddtrace.constants import MANUAL_DROP_KEY

        if sys.version_info >= (3, 7, 0):
            (w,) = ws
            assert issubclass(w.category, DeprecationWarning)
            assert "constant ENV_KEY has been deprecated and will be removed in v1.0" == str(w.message)
