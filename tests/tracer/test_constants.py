import warnings

import pytest

from ddtrace import DDTraceDeprecationWarning


def test_deprecated():
    import ddtrace

    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")

        assert ddtrace.constants.FILTERS_KEY

        (w,) = ws
        assert issubclass(w.category, DDTraceDeprecationWarning)
        assert "ddtrace.constants.FILTERS_KEY is deprecated and will be removed in version '1.0.0'" == str(w.message)

    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")

        assert ddtrace.constants.NUMERIC_TAGS

        (w,) = ws
        assert issubclass(w.category, DDTraceDeprecationWarning)
        assert "ddtrace.constants.NUMERIC_TAGS is deprecated and will be removed in version '1.0.0'" == str(w.message)

    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")

        assert ddtrace.constants.LOG_SPAN_KEY

        (w,) = ws
        assert issubclass(w.category, DDTraceDeprecationWarning)
        assert "ddtrace.constants.LOG_SPAN_KEY is deprecated and will be removed in version '1.0.0'" == str(w.message)


def test_not_deprecated():
    import ddtrace

    with warnings.catch_warnings(record=True) as ws:
        warnings.simplefilter("always")

        assert ddtrace.constants.ENV_KEY
        assert len(ws) == 0


def test_invalid():
    with pytest.raises(ImportError):
        from ddtrace.constants import INVALID_CONSTANT  # noqa
