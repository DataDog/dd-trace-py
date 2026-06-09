import sys
from unittest import mock

import pytest

from ddtrace.contrib.internal.pytorch import _rank_root


def _fresh_module():
    """Import _c_tracer with a clean module cache so _loaded resets."""
    sys.modules.pop("ddtrace.contrib.internal.pytorch._c_tracer", None)
    from ddtrace.contrib.internal.pytorch import _c_tracer

    return _c_tracer


def _make_fake_lib():
    lib = mock.MagicMock()
    lib.dd_set_global_parent_context = mock.MagicMock()
    lib.dd_set_global_parent_context.restype = None
    lib.dd_clear_global_parent_context = mock.MagicMock()
    lib.dd_clear_global_parent_context.restype = None
    return lib


def _make_absent_lib():
    """A lib handle where the C tracer symbols are not present."""
    lib = mock.MagicMock()
    type(lib).dd_set_global_parent_context = mock.PropertyMock(side_effect=AttributeError)
    return lib


def _make_fake_span(trace_id=0xDEADBEEF00000001, span_id=0xCAFE, sampling_priority=1):
    span = mock.Mock()
    span.trace_id = trace_id
    span.span_id = span_id
    span.context.sampling_priority = sampling_priority
    return span


# ---------------------------------------------------------------------------
# _load() — symbol presence determines no-op vs. active path
# ---------------------------------------------------------------------------


def test_set_parent_context_no_op_when_library_absent():
    mod = _fresh_module()
    mod._loaded = False
    with mock.patch("ctypes.CDLL", return_value=_make_absent_lib()):
        fake_span = mock.Mock()
        fake_span.trace_id = 0xABC
        fake_span.span_id = 0x123
        fake_span.context.sampling_priority = None
        mod.set_parent_context(fake_span, {"rank": 0, "world_size": 1, "framework": "ddp", "training_job_id": "j1"})


def test_clear_parent_context_no_op_when_library_absent():
    mod = _fresh_module()
    mod._loaded = False
    with mock.patch("ctypes.CDLL", return_value=_make_absent_lib()):
        mod.clear_parent_context()


def test_load_uses_global_symbol_table():
    """_load() calls ctypes.CDLL(None) — no library path, no discovery."""
    mod = _fresh_module()
    mod._loaded = False
    fake_lib = _make_fake_lib()
    with mock.patch("ctypes.CDLL", return_value=fake_lib) as mock_cdll:
        mod._load()
    mock_cdll.assert_called_once_with(None)


# ---------------------------------------------------------------------------
# Correct C function dispatch
# ---------------------------------------------------------------------------


def test_set_parent_context_calls_c_function():
    mod = _fresh_module()
    mod._loaded = False
    fake_lib = _make_fake_lib()
    with mock.patch("ctypes.CDLL", return_value=fake_lib):
        span = _make_fake_span(trace_id=0x00000001_DEADBEEF, span_id=0xCAFE, sampling_priority=2)
        mod.set_parent_context(span, {"rank": 3, "world_size": 8, "framework": "ddp", "training_job_id": "job-xyz"})
        assert fake_lib.dd_set_global_parent_context.called


def test_set_parent_context_128bit_trace_id_split():
    """High 64 bits are correctly separated from low 64 bits."""
    mod = _fresh_module()
    mod._loaded = False
    fake_lib = _make_fake_lib()
    captured = {}

    def capture(*args, **kwargs):
        captured["args"] = args

    fake_lib.dd_set_global_parent_context.side_effect = capture

    with mock.patch("ctypes.CDLL", return_value=fake_lib):
        trace_id = (0xAAAA << 64) | 0xBBBB
        span = _make_fake_span(trace_id=trace_id, span_id=0x1111)
        mod.set_parent_context(span, {"rank": 0, "world_size": 1, "framework": "none", "training_job_id": ""})
        lo = captured["args"][0].value
        hi = captured["args"][1].value
        assert lo == 0xBBBB
        assert hi == 0xAAAA


def test_clear_parent_context_calls_c_function():
    mod = _fresh_module()
    mod._loaded = False
    fake_lib = _make_fake_lib()
    with mock.patch("ctypes.CDLL", return_value=fake_lib):
        mod._load()
        mod.clear_parent_context()
        assert fake_lib.dd_clear_global_parent_context.called


def test_set_parent_context_tag_payload():
    """Verify the 4 expected tags are sent with correct values."""
    mod = _fresh_module()
    mod._loaded = False
    fake_lib = _make_fake_lib()
    captured = {}

    def capture(*args, **kwargs):
        captured["args"] = args

    fake_lib.dd_set_global_parent_context.side_effect = capture

    with mock.patch("ctypes.CDLL", return_value=fake_lib):
        span = _make_fake_span(trace_id=1, span_id=2, sampling_priority=1)
        mod.set_parent_context(
            span,
            {
                "training_job_id": "job-abc",
                "rank": 3,
                "world_size": 8,
                "framework": "fsdp",
            },
        )

    args = captured["args"]
    count = args[7].value  # c_size_t
    assert count == 4

    keys = [args[5][i].decode() for i in range(count)]
    vals = [args[6][i].decode() for i in range(count)]
    tag_map = dict(zip(keys, vals))

    assert tag_map["training_job_id"] == "job-abc"
    assert tag_map["rank"] == "3"
    assert tag_map["world_size"] == "8"
    assert tag_map["framework"] == "fsdp"


def test_set_parent_context_swallows_exception():
    mod = _fresh_module()
    mod._loaded = True
    mod._lib = object()
    mod._set_fn = mock.Mock(side_effect=RuntimeError("boom"))
    mod.set_parent_context(_make_fake_span(), {})


def test_clear_parent_context_swallows_exception():
    mod = _fresh_module()
    mod._loaded = True
    mod._lib = object()
    mod._clear_fn = mock.Mock(side_effect=RuntimeError("boom"))
    mod.clear_parent_context()


# ---------------------------------------------------------------------------
# Lifecycle integration: _rank_root calls _c_tracer at the right moments.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def _fresh_rank_root():
    """Reset _rank_root module state before and after each test."""
    import threading

    _rank_root._span = None
    _rank_root._lock = threading.Lock()
    _rank_root._atexit_registered = False
    if _rank_root._rotation_timer is not None:
        _rank_root._rotation_timer.cancel()
    _rank_root._rotation_timer = None
    _rank_root._open_kwargs = {}
    yield
    if _rank_root._rotation_timer is not None:
        _rank_root._rotation_timer.cancel()
    _rank_root._span = None
    _rank_root._rotation_timer = None


def test_open_rank_span_calls_set_parent_context(_fresh_rank_root):
    with (
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._c_tracer") as mc,
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._build_span", return_value=_make_fake_span()),
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._schedule_rotation"),
    ):
        _rank_root.open_rank_span(rank=0, world_size=4, framework="ddp", training_job_id="job-1")
        assert mc.set_parent_context.called
        args = mc.set_parent_context.call_args[0]
        assert args[1]["framework"] == "ddp"
        assert args[1]["rank"] == 0


def test_close_calls_clear_parent_context(_fresh_rank_root):
    fake_span = _make_fake_span()
    fake_span.finish = mock.Mock()
    _rank_root._span = fake_span
    with (
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._c_tracer") as mc,
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._safe_flush"),
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._tag_ray_run_context"),
    ):
        _rank_root.close()
        assert mc.clear_parent_context.called


def test_rotate_span_updates_context_before_finishing_old(_fresh_rank_root):
    """set_parent_context(new) must be called BEFORE old_span.finish()."""
    call_order = []
    new_span = _make_fake_span(trace_id=2, span_id=200)
    old_span = _make_fake_span(trace_id=1, span_id=100)
    old_span.finish = mock.Mock(side_effect=lambda: call_order.append("finish"))
    old_span.set_tag = mock.Mock()

    _rank_root._span = old_span
    _rank_root._open_kwargs = {"rank": 0, "world_size": 1, "framework": "ddp", "training_job_id": "j"}

    def fake_set(span, kwargs):
        call_order.append(("set", span.span_id))

    with (
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._build_span", return_value=new_span),
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._schedule_rotation"),
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._safe_flush"),
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._tag_ray_run_context"),
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._c_tracer") as mc,
    ):
        mc.set_parent_context.side_effect = fake_set
        _rank_root._rotate_span()

    set_idx = next(i for i, x in enumerate(call_order) if isinstance(x, tuple) and x[0] == "set")
    fin_idx = call_order.index("finish")
    assert set_idx < fin_idx, f"set must precede finish; order={call_order}"


def test_close_clears_c_tracer_even_when_finish_raises(_fresh_rank_root):
    """clear_parent_context must be called even if span.finish() raises."""
    fake_span = _make_fake_span()
    fake_span.finish = mock.Mock(side_effect=RuntimeError("finish failed"))
    _rank_root._span = fake_span
    with (
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._c_tracer") as mc,
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._safe_flush"),
        mock.patch("ddtrace.contrib.internal.pytorch._rank_root._tag_ray_run_context"),
    ):
        _rank_root.close()  # must not raise
        assert mc.clear_parent_context.called, "clear must fire even when finish raises"


def test_set_framework_updates_c_tracer_context(_fresh_rank_root):
    fake_span = _make_fake_span()
    _rank_root._span = fake_span
    _rank_root._open_kwargs = {"rank": 0, "world_size": 1, "framework": "none", "training_job_id": "j"}
    with mock.patch("ddtrace.contrib.internal.pytorch._rank_root._c_tracer") as mc:
        _rank_root.set_framework("fsdp")
        assert mc.set_parent_context.called
        args = mc.set_parent_context.call_args[0]
        assert args[1]["framework"] == "fsdp"
