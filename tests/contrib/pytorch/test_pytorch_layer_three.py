"""Layer 3 (CUDA kernel profiling) unit + integration tests."""

import importlib
import threading


def _reload_profiler():
    """Reload `_profiler` so each test starts from a clean module state."""
    from ddtrace.contrib.internal.pytorch import _profiler

    return importlib.reload(_profiler)


def _make_event(name, start_us, end_us, stream=7, flops=None, device_type="cuda"):
    """Build a fake torch.profiler event for unit tests."""

    class Range:
        def __init__(self, s, e):
            self.start, self.end = s, e

    class Ev:
        pass

    ev = Ev()
    ev.name = name
    ev.time_range = Range(start_us, end_us)
    ev.stream = stream
    ev.flops = flops
    ev.device_type = device_type
    return ev


def test_kernel_profiling_gate_default_false(monkeypatch):
    monkeypatch.delenv("DD_PYTORCH_KERNEL_PROFILING", raising=False)
    prof = _reload_profiler()
    assert prof.KERNEL_PROFILING_ENABLED is False


def test_kernel_profiling_gate_true(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_KERNEL_PROFILING", "true")
    prof = _reload_profiler()
    assert prof.KERNEL_PROFILING_ENABLED is True


def test_kernel_profiling_gate_invalid_falls_back_to_false(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_KERNEL_PROFILING", "notabool")
    prof = _reload_profiler()
    assert prof.KERNEL_PROFILING_ENABLED is False


def test_schedule_defaults(monkeypatch):
    for v in (
        "DD_PYTORCH_PROFILE_WAIT_STEPS",
        "DD_PYTORCH_PROFILE_WARMUP_STEPS",
        "DD_PYTORCH_PROFILE_ACTIVE_STEPS",
    ):
        monkeypatch.delenv(v, raising=False)
    prof = _reload_profiler()
    assert prof._read_schedule_config() == (99, 1, 5)


def test_schedule_overrides(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_PROFILE_WAIT_STEPS", "10")
    monkeypatch.setenv("DD_PYTORCH_PROFILE_WARMUP_STEPS", "2")
    monkeypatch.setenv("DD_PYTORCH_PROFILE_ACTIVE_STEPS", "3")
    prof = _reload_profiler()
    assert prof._read_schedule_config() == (10, 2, 3)


def test_schedule_bad_input_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("DD_PYTORCH_PROFILE_WAIT_STEPS", "notanint")
    monkeypatch.setenv("DD_PYTORCH_PROFILE_WARMUP_STEPS", "-1")
    monkeypatch.setenv("DD_PYTORCH_PROFILE_ACTIVE_STEPS", "0")
    prof = _reload_profiler()
    with caplog.at_level("WARNING"):
        assert prof._read_schedule_config() == (99, 1, 5)


def _entry(prof, step, start, end, rank=0, trace_id=1, span_id=1):
    return prof.RingBufferEntry(step=step, rank=rank, trace_id=trace_id, span_id=span_id, start_ns=start, end_ns=end)


def test_ring_buffer_insert_and_find():
    prof = _reload_profiler()
    buf = prof.StepRingBuffer(capacity=4)
    buf.append(_entry(prof, 1, 100, 200))
    buf.append(_entry(prof, 2, 300, 400))
    assert buf.find_for_timestamp(150).step == 1
    assert buf.find_for_timestamp(350).step == 2
    assert buf.find_for_timestamp(250) is None


def test_ring_buffer_eviction_drops_oldest():
    prof = _reload_profiler()
    buf = prof.StepRingBuffer(capacity=2)
    buf.append(_entry(prof, 1, 100, 200))
    buf.append(_entry(prof, 2, 300, 400))
    buf.append(_entry(prof, 3, 500, 600))
    assert buf.find_for_timestamp(150) is None
    assert buf.find_for_timestamp(350).step == 2
    assert buf.find_for_timestamp(550).step == 3


def test_ring_buffer_concurrent_writers_and_reader():
    prof = _reload_profiler()
    buf = prof.StepRingBuffer(capacity=200)
    barrier = threading.Barrier(3)

    def writer(start_step):
        barrier.wait()
        for i in range(50):
            s = start_step + i
            buf.append(_entry(prof, s, s * 1000, s * 1000 + 500))

    def reader(results):
        barrier.wait()
        for _ in range(50):
            results.append(buf.find_for_timestamp(10_000 + 100))

    results: list = []
    t1 = threading.Thread(target=writer, args=(0,))
    t2 = threading.Thread(target=writer, args=(1000,))
    t3 = threading.Thread(target=reader, args=(results,))
    for t in (t1, t2, t3):
        t.start()
    for t in (t1, t2, t3):
        t.join()
    assert len(buf) <= 200


def test_on_designated_step_finished_appends_and_steps(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_KERNEL_PROFILING", "true")
    prof = _reload_profiler()
    calls = {"step": 0}

    class FakeProf:
        def step(self):
            calls["step"] += 1

    prof._STATE.profiler = FakeProf()
    prof._STATE.ring_buffer = prof.StepRingBuffer(capacity=16)

    class Span:
        def __init__(self, tid, sid, start, dur):
            self.trace_id, self.span_id = tid, sid
            self.start_ns, self.duration_ns = start, dur

    prof.on_designated_step_finished(span=Span(7, 70, 1000, 1000), step=1, rank=0)

    entries = prof._STATE.ring_buffer.snapshot()
    assert calls["step"] == 1
    assert len(entries) == 1
    assert entries[0].trace_id == 7 and entries[0].span_id == 70
    assert entries[0].start_ns == 1000 and entries[0].end_ns == 2000


def test_on_designated_step_noop_when_gate_off(monkeypatch):
    monkeypatch.delenv("DD_PYTORCH_KERNEL_PROFILING", raising=False)
    prof = _reload_profiler()

    class Span:
        trace_id = 1
        span_id = 1
        start_ns = 0
        duration_ns = 0

    prof.on_designated_step_finished(span=Span(), step=0, rank=0)
    assert prof._STATE.ring_buffer is None


def test_profiler_lazy_build_idempotent(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_KERNEL_PROFILING", "true")
    prof = _reload_profiler()
    built = {"count": 0, "stopped": False}

    class FakeProf:
        def __init__(self, **kwargs):
            built["count"] += 1

        def start(self):
            pass

        def stop(self):
            built["stopped"] = True

        def step(self):
            pass

    monkeypatch.setattr(prof, "_build_torch_profiler", lambda **kw: FakeProf(**kw))
    monkeypatch.setattr(prof, "_resolve_activities", lambda: [])

    prof.ensure_profiler_started(rank=0)
    prof.ensure_profiler_started(rank=0)  # idempotent
    assert built["count"] == 1
    assert prof._STATE.ring_buffer is not None
    # capacity = wait + warmup + active + safety_margin*active = 99+1+5+10 = 115
    assert prof._STATE.ring_buffer._capacity >= 99 + 1 + 5 + 2 * 5

    prof.shutdown_profiler()
    assert built["stopped"] is True
    assert prof._STATE.profiler is None


def test_unsupported_platform_falls_back_gracefully(monkeypatch, caplog):
    monkeypatch.setenv("DD_PYTORCH_KERNEL_PROFILING", "true")
    prof = _reload_profiler()

    def boom(**kwargs):
        raise RuntimeError("no CUPTI on this platform")

    monkeypatch.setattr(prof, "_build_torch_profiler", boom)
    monkeypatch.setattr(prof, "_resolve_activities", lambda: [])

    with caplog.at_level("INFO"):
        prof.ensure_profiler_started(rank=0)
    assert prof._STATE.profiler is None

    # Subsequent step notifications must not crash.
    class Span:
        trace_id = 1
        span_id = 1
        start_ns = 0
        duration_ns = 100

    prof.on_designated_step_finished(span=Span(), step=0, rank=0)


def test_on_trace_ready_drops_unmatched_kernels_and_counts_misses(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_KERNEL_PROFILING", "true")
    prof = _reload_profiler()
    prof._STATE.ring_buffer = prof.StepRingBuffer(capacity=8)
    prof._STATE.ring_buffer.append(
        prof.RingBufferEntry(step=1, rank=0, trace_id=11, span_id=22, start_ns=1_000_000, end_ns=2_000_000)
    )
    prof._STATE.clock_offset_ns = 0
    # Suppress the periodic clock-offset refresh so our test offset stays at 0.
    prof._STATE.last_offset_refresh_ns = 2**62

    class FakeProf:
        def events(self):
            return [
                _make_event("hit", 1500, 1700),  # 1500us*1000 = 1_500_000 ns -> hit
                _make_event("miss", 9_000_000, 9_500_000),  # outside window
            ]

    emitted = []
    monkeypatch.setattr(prof, "_emit_kernel_span", lambda **kw: emitted.append(kw))
    prof._on_trace_ready(FakeProf(), rank=0)
    assert len(emitted) == 1
    assert prof._STATE.miss_counter == 1


def test_on_trace_ready_reentry_safe(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_KERNEL_PROFILING", "true")
    prof = _reload_profiler()
    prof._STATE.ring_buffer = prof.StepRingBuffer(capacity=8)
    prof._STATE.clock_offset_ns = 0
    prof._STATE.last_offset_refresh_ns = 2**62

    class FakeProf:
        def events(self):
            return [_make_event("k", 9_000_000, 9_500_000)]  # always miss

    monkeypatch.setattr(prof, "_emit_kernel_span", lambda **kw: None)
    barrier = threading.Barrier(2)

    def run():
        barrier.wait()
        prof._on_trace_ready(FakeProf(), rank=0)

    t1 = threading.Thread(target=run)
    t2 = threading.Thread(target=run)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert prof._STATE.miss_counter == 2


def test_on_trace_ready_ignores_cpu_events(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_KERNEL_PROFILING", "true")
    prof = _reload_profiler()
    prof._STATE.ring_buffer = prof.StepRingBuffer(capacity=8)
    prof._STATE.ring_buffer.append(
        prof.RingBufferEntry(step=1, rank=0, trace_id=11, span_id=22, start_ns=1_000_000, end_ns=2_000_000)
    )
    prof._STATE.clock_offset_ns = 0
    prof._STATE.last_offset_refresh_ns = 2**62

    class FakeProf:
        def events(self):
            return [_make_event("k", 1500, 1700, device_type="cpu")]

    emitted = []
    monkeypatch.setattr(prof, "_emit_kernel_span", lambda **kw: emitted.append(kw))
    prof._on_trace_ready(FakeProf(), rank=0)
    assert emitted == []


def test_clock_alignment_math(monkeypatch):
    """Clock alignment: aligned_ns = event_us * 1000 + offset_ns."""
    monkeypatch.setenv("DD_PYTORCH_KERNEL_PROFILING", "true")
    prof = _reload_profiler()
    prof._STATE.ring_buffer = prof.StepRingBuffer(capacity=4)
    prof._STATE.clock_offset_ns = 1_000_000_000_000  # 1000s in ns
    # Entry window covers ~1ms around the aligned kernel timestamp.
    prof._STATE.ring_buffer.append(
        prof.RingBufferEntry(
            step=1,
            rank=0,
            trace_id=1,
            span_id=2,
            start_ns=1_001_000_000_000,
            end_ns=1_002_000_000_000,
        )
    )

    class FakeProf:
        def events(self):
            # event.time_range.start in μs on internal clock; 1_500_000 μs * 1000 = 1.5e12 ns
            # + offset 1e12 = 2.5e12 ns. Whoops — would land outside entry.
            # We need event_us * 1000 + offset to land inside [1_001_000_000_000, 1_002_000_000_000].
            # event_us = 1_500_000 → 1_500_000 * 1000 + 1e12 = 1.5e12 + 1e12 = 2.5e12, NO.
            # Use smaller event_us: 1500 * 1000 + 1e12 = 1_500_000 + 1e12 ≈ 1.000001e12 (way outside).
            # The entry must be in the "1e12 + ~ms" range. event_us = 1_500_000 means 1.5s in μs.
            # aligned = 1.5e6 * 1e3 + 1e12 = 1.5e9 + 1e12 = 1_001_500_000_000. INSIDE [1.001e12, 1.002e12].
            return [_make_event("k", 1_500_000, 1_500_200)]

    emitted = []
    monkeypatch.setattr(
        prof,
        "_emit_kernel_span",
        lambda **kw: emitted.append((kw["start_ns"], kw["end_ns"])),
    )
    prof._STATE.last_offset_refresh_ns = 2**62
    prof._on_trace_ready(FakeProf(), rank=0)
    assert emitted == [(1_001_500_000_000, 1_001_500_200_000)]


def test_clock_offset_refresh(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_KERNEL_PROFILING", "true")
    prof = _reload_profiler()
    prof._STATE.last_offset_refresh_ns = 0
    prof._STATE.offset_refresh_interval_ns = 1
    seen = {"called": 0}

    def fake_offset():
        seen["called"] += 1
        from ddtrace.contrib.internal.pytorch._utils import ClockOffset

        return ClockOffset(offset_ns=12345, uncertainty_ns=0)

    monkeypatch.setattr("ddtrace.contrib.internal.pytorch._profiler.compute_clock_offset_ns", fake_offset)
    monkeypatch.setattr("time.time_ns", lambda: 100)
    prof._maybe_refresh_clock_offset()
    assert seen["called"] == 1
    assert prof._STATE.clock_offset_ns == 12345


def test_emit_kernel_span_tags_and_metrics(monkeypatch):
    prof = _reload_profiler()

    captured: dict = {}

    class FakeSpan:
        def __init__(self):
            self.tags = {}
            self.metrics = {}

        def set_tag_str(self, k, v):
            self.tags[k] = v

        def _set_attribute(self, k, v):
            self.metrics[k] = v

        def finish(self, finish_time=None):
            captured["finish_time"] = finish_time

    class FakeTracer:
        def start_span(self, name, service=None, child_of=None, activate=False):
            captured["name"] = name
            captured["service"] = service
            captured["child_of"] = child_of
            captured["span"] = FakeSpan()
            return captured["span"]

    monkeypatch.setattr(prof, "_get_tracer", lambda: FakeTracer())

    entry = prof.RingBufferEntry(step=42, rank=0, trace_id=111, span_id=222, start_ns=1_000, end_ns=2_000)
    ev = _make_event("matmul_kernel", 1100, 1500, stream=9, flops=1024.0)
    prof._emit_kernel_span(event=ev, entry=entry, start_ns=1_100, end_ns=1_500, rank=0)
    span = captured["span"]
    assert captured["name"] == "pytorch.kernel"
    # Kernel spans must carry the pytorch service explicitly because the
    # `Context` parent carries no service to inherit.
    assert captured["service"] == "pytorch"
    assert captured["child_of"].trace_id == 111
    assert captured["child_of"].span_id == 222
    assert span.tags["kernel.name"] == "matmul_kernel"
    assert span.tags["stream_id"] == "9"
    assert span.metrics["duration_ms"] == (1500 - 1100) / 1e6
    assert span.metrics["flops"] == 1024.0
    assert span.metrics["step"] == 42
    assert span.metrics["rank"] == 0
    # training_job.id and job_id are set explicitly via set_training_job_id_tag.
    # When no job id is cached (default in unit tests), neither tag is set.
    assert "training_job.id" not in span.tags
    assert "job_id" not in span.tags
    # The Context passed as `child_of` must carry sampling_priority=USER_KEEP
    # so the kernel child isn't dropped after the parent step span has flushed.
    from ddtrace.constants import USER_KEEP

    assert captured["child_of"].sampling_priority == USER_KEEP


def test_kernel_span_carries_training_job_id(monkeypatch):
    """_emit_kernel_span must tag the span with both `training_job.id` and
    `job_id` when a job id is cached in _utils.
    """
    prof = _reload_profiler()

    from ddtrace.contrib.internal.pytorch import _utils

    _utils.set_cached_job_id("kernel-job-77")
    try:
        captured: dict = {}

        class FakeSpan:
            def __init__(self):
                self.tags: dict = {}
                self.metrics: dict = {}
                self.start_ns: int = 0

            def set_tag_str(self, k, v):
                self.tags[k] = v

            def set_tag(self, k, v):
                self.tags[k] = v

            def _set_attribute(self, k, v):
                self.metrics[k] = v

            def finish(self, finish_time=None):
                pass

        class FakeTracer:
            def start_span(self, name, service=None, child_of=None, activate=False):
                captured["span"] = FakeSpan()
                return captured["span"]

        monkeypatch.setattr(prof, "_get_tracer", lambda: FakeTracer())

        entry = prof.RingBufferEntry(step=1, rank=0, trace_id=10, span_id=20, start_ns=1_000, end_ns=2_000)
        ev = _make_event("conv_kernel", 1100, 1500)
        prof._emit_kernel_span(event=ev, entry=entry, start_ns=1_100, end_ns=1_500, rank=0)
        span = captured["span"]
        assert span.tags.get("training_job.id") == "kernel-job-77"
        assert span.tags.get("job_id") == "kernel-job-77"
    finally:
        _utils.set_cached_job_id(None)


def test_amp_skipped_steps_do_not_advance_profiler(monkeypatch):
    """Skipped steps don't go through `_maybe_close_step` (the GradScaler
    wrapper calls `_close_step(skipped=True)` directly), so the Layer 3 hook
    isn't fired. The profiler captures only non-skipped steps.
    """
    monkeypatch.setenv("DD_PYTORCH_KERNEL_PROFILING", "true")
    prof = _reload_profiler()
    calls = {"step": 0}

    class FakeProf:
        def step(self):
            calls["step"] += 1

    prof._STATE.profiler = FakeProf()
    prof._STATE.ring_buffer = prof.StepRingBuffer(capacity=16)

    class Span:
        def __init__(self, tid, sid, start, dur):
            self.trace_id, self.span_id = tid, sid
            self.start_ns, self.duration_ns = start, dur

    # Simulate three real (non-skipped) steps; the hook only fires on those.
    for i in range(3):
        prof.on_designated_step_finished(span=Span(1, i + 1, i * 1000, 500), step=i, rank=0)
    assert calls["step"] == 3
    assert len(prof._STATE.ring_buffer) == 3
