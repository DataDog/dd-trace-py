from ddtrace.ext.test_visibility import api as ext_api


# TODO(vitor-de-araujo): InternalTestId exists for historical reasons; it used to consist of TestId + EFD retry number.
# The retry number is not part of the test id anymore, so these types can be unified, and in the future removed.
InternalTestId = ext_api.TestId
