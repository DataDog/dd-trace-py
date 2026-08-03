# DEPRECATED: This module is scheduled for removal in dd-trace-py 5.0.0.
# Use DD_PYTEST_USE_NEW_PLUGIN=true (or unset; it is now the default) to opt into
# the new plugin at ddtrace/testing/internal/pytest/.
FRAMEWORK = "pytest"
KIND = "test"


# XFail Reason
XFAIL_REASON = "pytest.xfail.reason"

ITR_MIN_SUPPORTED_VERSION = (7, 2, 0)
RETRIES_MIN_SUPPORTED_VERSION = (7, 0, 0)
EFD_MIN_SUPPORTED_VERSION = RETRIES_MIN_SUPPORTED_VERSION
ATR_MIN_SUPPORTED_VERSION = RETRIES_MIN_SUPPORTED_VERSION
ATTEMPT_TO_FIX_MIN_SUPPORTED_VERSION = RETRIES_MIN_SUPPORTED_VERSION

USER_PROPERTY_QUARANTINED = ("dd_quarantined", True)
