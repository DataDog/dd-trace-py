from ddtrace.appsec._iast._taint_tracking._context import debug_num_tainted_objects
from ddtrace.appsec._iast_request_state import _get_iast_context_id


def _num_objects_tainted_in_request() -> int:
    """Return the number of objects tainted in the current request."""
    context_id = _get_iast_context_id()
    if context_id is not None:
        num_tainted: int = debug_num_tainted_objects(context_id)
        return num_tainted
    return 0
