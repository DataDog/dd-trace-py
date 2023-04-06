import warnings

import pytest


def test_deprecated():
    import ddtrace.constants

    message = " is deprecated and will be removed in version '2.0.0'"

    with warnings.catch_warnings(record=True) as warns:
        warnings.simplefilter("always")
        assert ddtrace.constants.APPSEC_ENABLED
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_ENABLED" + message == str(warn.message)

        assert ddtrace.constants.APPSEC_JSON
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_JSON" + message == str(warn.message)

        assert ddtrace.constants.APPSEC_ENABLED
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_ENABLED" + message == str(warn.message)

        assert ddtrace.constants.APPSEC_EVENT_RULE_VERSION
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_EVENT_RULE_VERSION" + message == str(warn.message)

        assert ddtrace.constants.APPSEC_EVENT_RULE_ERRORS
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_EVENT_RULE_ERRORS" + message == str(warn.message)

        assert ddtrace.constants.APPSEC_EVENT_RULE_LOADED
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_EVENT_RULE_LOADED" + message == str(warn.message)

        assert ddtrace.constants.APPSEC_EVENT_RULE_ERROR_COUNT
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_EVENT_RULE_ERROR_COUNT" + message == str(warn.message)

        assert ddtrace.constants.APPSEC_WAF_DURATION
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_WAF_DURATION" + message == str(warn.message)

        assert ddtrace.constants.APPSEC_WAF_DURATION_EXT
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_WAF_DURATION_EXT" + message == str(warn.message)

        assert ddtrace.constants.APPSEC_WAF_TIMEOUTS
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_WAF_TIMEOUTS" + message == str(warn.message)

        assert ddtrace.constants.APPSEC_WAF_VERSION
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_WAF_VERSION" + message == str(warn.message)

        assert ddtrace.constants.APPSEC_ORIGIN_VALUE
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_ORIGIN_VALUE" + message == str(warn.message)

        assert ddtrace.constants.APPSEC_BLOCKED
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.APPSEC_BLOCKED" + message == str(warn.message)

        assert ddtrace.constants.IAST_JSON
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.IAST_JSON" + message == str(warn.message)

        assert ddtrace.constants.IAST_ENABLED
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.IAST_ENABLED" + message == str(warn.message)

        assert ddtrace.constants.IAST_CONTEXT_KEY
        warn = warns.pop()
        assert issubclass(warn.category, DeprecationWarning)
        assert "ddtrace.constants.IAST_CONTEXT_KEY" + message == str(warn.message)


def test_not_deprecated():
    import ddtrace.constants

    with warnings.catch_warnings(record=True) as warns:
        warnings.simplefilter("always")
        assert ddtrace.constants.IAST_ENV
        assert ddtrace.constants.APPSEC_ENV
        assert len(warns) == 0


def test_invalid():
    with pytest.raises(ImportError):
        from ddtrace.constants import INVALID_CONSTANT  # noqa
