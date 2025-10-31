from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request
from ddtrace.appsec._iast._iast_request_context_base import _num_objects_tainted_in_request
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking._context import clear_all_request_context_slots
from ddtrace.appsec._iast._taint_tracking._context import debug_num_tainted_objects
from ddtrace.appsec._iast._taint_tracking._context import finish_request_context
from ddtrace.appsec._iast._taint_tracking._context import start_request_context
from ddtrace.appsec._iast._taint_tracking._taint_objects import copy_ranges_to_string
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject_with_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import _taint_pyobject_base
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from tests.appsec.iast.iast_utils import _end_iast_context_and_oce
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce


def test_create_two_contexts_and_they_are_distinct():
    clear_all_request_context_slots()
    ctx1 = start_request_context()
    ctx2 = start_request_context()
    assert ctx1 is not None
    assert ctx2 is not None
    assert ctx1 != ctx2


def test_reuse_freed_slot_lowest_index():
    clear_all_request_context_slots()
    c1 = start_request_context()
    c2 = start_request_context()
    assert c1 is not None and c2 is not None and c1 != c2

    # Free the first and ensure reuse on next start
    finish_request_context(c1)
    assert debug_num_tainted_objects(c1) == 0
    c3 = start_request_context()
    assert c3 == c1


def test_finish_none_context_is_noop():
    # Treat deleting a None context as a silent no-op by using
    # _iast_finish_request() without an active context.
    clear_all_request_context_slots()
    _end_iast_context_and_oce()
    assert _iast_finish_request(None) is False  # no active context -> no-op


def test_finish_invalid_context_id_is_noop():
    clear_all_request_context_slots()
    # Very large id should be ignored by native layer
    invalid_id = 10_000_000
    # No exception should be raised
    finish_request_context(invalid_id)
    # And querying num tainted objects for that id should not crash (0 by design if ever checked)
    # Note: debug_num_tainted_objects bounds-checks via get_tainted_object_map_by_ctx_id
    # We avoid calling it directly for out-of-range to mirror C++ behavior tests.


def test_finish_context_twice_is_noop():
    clear_all_request_context_slots()
    ctx = start_request_context()
    assert ctx is not None
    finish_request_context(ctx)
    # second finish is idempotent
    finish_request_context(ctx)
    # slot remains cleared
    # We can't directly check the map presence here, but re-starting should reuse the same id
    new_id = start_request_context()
    assert new_id == ctx


def test_taint_object_with_no_context_is_noop():
    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    arg = "hello"
    tainted = taint_pyobject(arg, source_name="request_body", source_value=arg, source_origin=OriginType.PARAMETER)
    assert tainted == arg
    assert _num_objects_tainted_in_request() == 0

    # taint with ranges is also a no-op returning False
    from ddtrace.appsec._iast._taint_tracking import Source as TaintRangeSource

    res = taint_pyobject_with_ranges(
        arg,
        [TaintRange(0, len(arg), TaintRangeSource(arg, "request_body", OriginType.PARAMETER), [])],
    )
    assert res is False


def test_taint_with_wrong_context_id_is_noop():
    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    s = "abc"
    invalid_ctx = 999_999
    res = _taint_pyobject_base(s, "src", "val", OriginType.PARAMETER, contextid=invalid_ctx)
    assert res == s
    assert not is_pyobject_tainted(res)


def test_get_tainted_ranges_returns_empty_without_context():
    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    assert get_tainted_ranges("xyz") == tuple()


def test_in_taint_map_scans_container_across_active_maps():
    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    # Start two independent maps
    ctx1 = start_request_context()
    ctx2 = start_request_context()
    assert ctx1 is not None and ctx2 is not None and ctx1 != ctx2

    target = "TAINT_ME"
    # Taint the string into ctx1 explicitly without setting ContextVar
    target_tainted1 = _taint_pyobject_base(target, "p", target, OriginType.PARAMETER, contextid=ctx1)
    target_tainted2 = _taint_pyobject_base(target, "p", target, OriginType.PARAMETER, contextid=ctx2)

    assert is_pyobject_tainted(target) is False
    assert is_pyobject_tainted(target_tainted1) is True
    assert is_pyobject_tainted(target_tainted2) is True


def test_num_objects_tainted_is_per_current_context():
    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    # Activate ctx1
    _start_iast_context_and_oce()
    assert _num_objects_tainted_in_request() == 0
    s1 = taint_pyobject("x", "src", "x", OriginType.PARAMETER)
    assert is_pyobject_tainted(s1)
    assert _num_objects_tainted_in_request() == 1
    _end_iast_context_and_oce()

    # Activate ctx2
    _start_iast_context_and_oce()
    s2a = taint_pyobject("y1", "src", "y1", OriginType.PARAMETER)
    s2b = taint_pyobject("y2", "src", "y2", OriginType.PARAMETER)
    assert is_pyobject_tainted(s2a) and is_pyobject_tainted(s2b)
    assert _num_objects_tainted_in_request() == 2
    _end_iast_context_and_oce()

    # No active context => 0
    assert _num_objects_tainted_in_request() == 0


def test_is_pyobject_tainted_false_for_non_taintable_types_without_context():
    clear_all_request_context_slots()
    _end_iast_context_and_oce()
    assert is_pyobject_tainted(123) is False
    assert is_pyobject_tainted(object()) is False


def test_copy_ranges_to_string_without_context_is_noop():
    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    from ddtrace.appsec._iast._taint_tracking import Source as TaintRangeSource

    src_val = "abc"
    ranges = [TaintRange(0, len(src_val), TaintRangeSource(src_val, "param", OriginType.PARAMETER), [])]
    out = copy_ranges_to_string("zzz", ranges)
    assert out == "zzz"
    assert is_pyobject_tainted(out) is False
