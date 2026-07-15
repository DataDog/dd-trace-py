import gc

from ddtrace.appsec._iast._iast_request_context_base import IAST_CONTEXT
from ddtrace.appsec._iast._iast_request_context_base import _iast_finish_request
from ddtrace.appsec._iast._iast_request_context_base import _num_objects_tainted_in_request
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source as TaintRangeSource
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import get_ranges
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

    res = taint_pyobject_with_ranges(
        arg,
        (TaintRange(0, len(arg), TaintRangeSource(arg, "request_body", OriginType.PARAMETER), []),),
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


def test_is_pyobject_tainted_is_scoped_to_active_slot():
    """Taint queries resolve only within the active request slot."""
    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    ctx1 = start_request_context()
    ctx2 = start_request_context()
    assert ctx1 is not None and ctx2 is not None and ctx1 != ctx2

    # Distinct values -> distinct objects, so each lives in exactly one slot.
    tainted_in_1 = _taint_pyobject_base("TAINT_ONE", "p", "TAINT_ONE", OriginType.PARAMETER, contextid=ctx1)
    tainted_in_2 = _taint_pyobject_base("TAINT_TWO", "p", "TAINT_TWO", OriginType.PARAMETER, contextid=ctx2)

    IAST_CONTEXT.set(ctx1)
    assert is_pyobject_tainted(tainted_in_1) is True
    assert is_pyobject_tainted(tainted_in_2) is False

    IAST_CONTEXT.set(ctx2)
    assert is_pyobject_tainted(tainted_in_2) is True
    assert is_pyobject_tainted(tainted_in_1) is False

    # No active request => queries short-circuit to False (native is not consulted).
    IAST_CONTEXT.set(None)
    assert is_pyobject_tainted(tainted_in_1) is False
    assert is_pyobject_tainted(tainted_in_2) is False

    finish_request_context(ctx1)
    finish_request_context(ctx2)
    IAST_CONTEXT.set(None)


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

    src_val = "abc"
    ranges = [TaintRange(0, len(src_val), TaintRangeSource(src_val, "param", OriginType.PARAMETER), [])]
    out = copy_ranges_to_string("zzz", ranges)
    assert out == "zzz"
    assert is_pyobject_tainted(out) is False


def test_is_pyobject_tainted_does_not_leak_across_request_slots():
    """A PyObject tainted in one request slot must not appear tainted from another slot."""
    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    ctx_a = start_request_context()
    assert ctx_a is not None

    tainted_in_a = _taint_pyobject_base(
        "http://dummy.location.com",
        "location",
        "http://dummy.location.com",
        OriginType.PARAMETER,
        contextid=ctx_a,
    )
    IAST_CONTEXT.set(ctx_a)
    assert is_pyobject_tainted(tainted_in_a) is True

    ctx_b = start_request_context()
    assert ctx_b is not None and ctx_b != ctx_a
    IAST_CONTEXT.set(ctx_b)

    assert is_pyobject_tainted(tainted_in_a) is False
    assert len(get_tainted_ranges(tainted_in_a)) == 0

    finish_request_context(ctx_a)
    finish_request_context(ctx_b)
    IAST_CONTEXT.set(None)


def test_get_ranges_public_api_does_not_leak_across_request_slots():
    """The public ``get_ranges`` (used by sinks and aspects) must also be slot-scoped."""

    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    ctx_a = start_request_context()
    assert ctx_a is not None
    tainted_in_a = _taint_pyobject_base(
        "http://dummy.location.com",
        "location",
        "http://dummy.location.com",
        OriginType.PARAMETER,
        contextid=ctx_a,
    )
    IAST_CONTEXT.set(ctx_a)
    assert len(get_ranges(tainted_in_a)) > 0

    ctx_b = start_request_context()
    assert ctx_b is not None and ctx_b != ctx_a
    IAST_CONTEXT.set(ctx_b)

    assert get_ranges(tainted_in_a) == []

    finish_request_context(ctx_a)
    finish_request_context(ctx_b)
    IAST_CONTEXT.set(None)


def test_copy_ranges_from_strings_writes_to_active_slot_not_earlier_slot():
    """copy_ranges_from_strings must write the derived taint into the active slot.

    Regression: the native multi-slot resolver picks the first slot holding the
    source id, so with a stale entry in an earlier slot the derived taint was
    written there and the scoped get_ranges() read from the active slot missed it.
    """
    from ddtrace.appsec._iast._taint_tracking import copy_ranges_from_strings
    from ddtrace.appsec._iast._taint_tracking import get_ranges
    from ddtrace.appsec._iast._taint_tracking import set_ranges

    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    ctx_a = start_request_context()
    ctx_b = start_request_context()
    assert ctx_a is not None and ctx_b is not None and ctx_a != ctx_b

    # The same source object lives in both slots, with ctx_a being the earlier slot.
    shared = _taint_pyobject_base("SECRET", "p", "SECRET", OriginType.PARAMETER, contextid=ctx_a)
    ranges_a = get_ranges(shared, ctx_a)
    assert len(ranges_a) > 0
    set_ranges(shared, ranges_a, ctx_b)
    assert len(get_ranges(shared, ctx_b)) > 0

    # Active request is the later slot ctx_b.
    IAST_CONTEXT.set(ctx_b)
    derived = "X" + shared  # a fresh, otherwise-untracked object
    copy_ranges_from_strings(shared, derived)

    # The derived taint must be visible from the active slot's scoped read.
    assert len(get_ranges(derived, ctx_b)) > 0

    finish_request_context(ctx_a)
    finish_request_context(ctx_b)
    IAST_CONTEXT.set(None)


def test_copy_and_shift_ranges_from_strings_writes_to_active_slot_not_earlier_slot():
    """copy_and_shift_ranges_from_strings must also be scoped to the active slot."""
    from ddtrace.appsec._iast._taint_tracking import copy_and_shift_ranges_from_strings
    from ddtrace.appsec._iast._taint_tracking import get_ranges
    from ddtrace.appsec._iast._taint_tracking import set_ranges

    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    ctx_a = start_request_context()
    ctx_b = start_request_context()
    assert ctx_a is not None and ctx_b is not None and ctx_a != ctx_b

    shared = _taint_pyobject_base("SECRET", "p", "SECRET", OriginType.PARAMETER, contextid=ctx_a)
    ranges_a = get_ranges(shared, ctx_a)
    assert len(ranges_a) > 0
    set_ranges(shared, ranges_a, ctx_b)
    assert len(get_ranges(shared, ctx_b)) > 0

    IAST_CONTEXT.set(ctx_b)
    derived = "X" + shared
    copy_and_shift_ranges_from_strings(shared, derived, 1, len(derived))

    assert len(get_ranges(derived, ctx_b)) > 0

    finish_request_context(ctx_a)
    finish_request_context(ctx_b)
    IAST_CONTEXT.set(None)

    
def test_is_pyobject_tainted_without_context_does_not_consult_native():
    """Regression for a teardown/GC crash (#18996).

    With no active request context the query must return False without
    entering native code.
    """
    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    ctx = start_request_context()
    assert ctx is not None
    tainted = _taint_pyobject_base(
        "http://dummy.location.com",
        "location",
        "http://dummy.location.com",
        OriginType.PARAMETER,
        contextid=ctx,
    )
    IAST_CONTEXT.set(ctx)
    assert is_pyobject_tainted(tainted) is True  # sanity: visible within its own slot

    # Detach the context, as happens once the request finishes but the tainted
    # object is still referenced by a soon-to-be-finalized structure.
    IAST_CONTEXT.set(None)
    assert is_pyobject_tainted(tainted) is False
    assert get_tainted_ranges(tainted) == tuple()

    finish_request_context(ctx)
    IAST_CONTEXT.set(None)


def test_taint_query_from_finalizer_without_context_is_safe():
    clear_all_request_context_slots()
    _end_iast_context_and_oce()
    IAST_CONTEXT.set(None)

    results = []

    class _QueriesTaintOnDel:
        def __init__(self, value):
            self.value = value

        def __del__(self):
            results.append(is_pyobject_tainted(self.value))

    obj = _QueriesTaintOnDel("some-tainted-looking-string")
    del obj
    gc.collect()

    assert results == [False]


def test_get_ranges_public_api_returns_empty_without_context():
    clear_all_request_context_slots()
    _end_iast_context_and_oce()

    ctx = start_request_context()
    assert ctx is not None
    tainted = _taint_pyobject_base("secret", "p", "secret", OriginType.PARAMETER, contextid=ctx)

    # The tainted object lives in a real slot, but with no active context the
    # public wrapper must not consult the native multi-slot resolver.
    IAST_CONTEXT.set(None)
    assert list(get_ranges(tainted)) == []

    finish_request_context(ctx)
    IAST_CONTEXT.set(None)
