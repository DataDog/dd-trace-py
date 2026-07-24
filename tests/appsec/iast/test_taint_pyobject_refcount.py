import pytest


@pytest.mark.xfail(
    strict=True,
    reason="api_taint_pyobject returns a borrowed input when its request slot is gone",
)
@pytest.mark.subprocess(err=None)
def test_taint_pyobject_in_copied_context_returns_owned_reference():
    import contextvars
    import os
    import sys

    from ddtrace.appsec._iast._iast_request_context_base import IAST_CONTEXT
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import initialize_native_state
    from ddtrace.appsec._iast._taint_tracking._context import finish_request_context
    from ddtrace.appsec._iast._taint_tracking._context import start_request_context
    from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject

    initialize_native_state()
    context_id = start_request_context()
    assert context_id is not None
    IAST_CONTEXT.set(context_id)
    copied_context = contextvars.copy_context()

    finish_request_context(context_id)
    IAST_CONTEXT.set(None)

    value = bytearray(b"customer input")
    refcount_before = sys.getrefcount(value)
    returned = copied_context.run(
        taint_pyobject,
        value,
        "parameter",
        "customer input",
        OriginType.PARAMETER,
    )
    refcount_after = sys.getrefcount(value)

    if returned is not value or refcount_after != refcount_before + 1:
        # AIDEV-NOTE: Avoid normal cleanup: both names appear to own the same
        # reference, which can turn this deterministic check into a UAF crash.
        os._exit(1)
