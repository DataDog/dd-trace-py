"""Stage 2 of OPTION_C_ONE_HOP_PLAN.md: dual-write equivalence harness.

Feeds identical synthetic samples into both the existing Cython/C++
`ddup.SampleHandle` and the new PyO3 `DdProfile`/`SampleHandle`, decodes both
resulting pprofs with the same helper the rest of the profiling test suite
already uses (`tests/profiling/collector/pprof_utils.py`), and diffs sample
values/labels/frames. This is the gate for every later stage: if these tests
don't pass, nothing downstream (wiring, benchmarking, cutover) is safe to do.

Each case runs in its own subprocess (like `test_upload_via_native_writes_pprof_and_info`
in `tests/profiling/exporter/test_ddup.py`) because `ddup.config()`+`ddup.start()`
initialize `ProfilerState` exactly once per process (`ddup_start()` is
`std::call_once`-guarded in `ddup_interface.cpp`): a later `ddup.config()` call
in the same process cannot change `max_nframes` or `timeline_enabled` for an
already-started profiler, so each configuration needs a fresh process.
"""

import pytest


native_profiling = pytest.importorskip(
    "ddtrace.internal.native._native", reason="requires the profiling feature of the _native extension"
)

pytestmark = pytest.mark.skipif(
    not hasattr(native_profiling, "DdProfile"), reason="DdProfile is only built with the profiling Cargo feature"
)


@pytest.mark.subprocess()
def test_dualwrite_sample_parity():
    import glob
    import tempfile

    import ddtrace
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.native import _native
    from tests.profiling.collector import pprof_utils

    def dump_samples(prof):
        """Decodes every pprof `Sample` into a comparable, order-independent dict."""
        out = []
        for sample in prof.sample:
            frames = []
            for location_id in sample.location_id:
                location = pprof_utils.get_location_with_id(prof, location_id)
                function = pprof_utils.get_function_with_id(prof, location.line[0].function_id)
                frames.append(
                    (prof.string_table[function.name], prof.string_table[function.filename], location.line[0].line)
                )
            labels = sorted(
                (prof.string_table[label.key], prof.string_table[label.str] if label.str else label.num)
                for label in sample.label
            )
            sample_type_names = [prof.string_table[st.type] for st in prof.sample_type]
            values = dict(zip(sample_type_names, sample.value))
            out.append({"frames": frames, "labels": labels, "values": values})
        return out

    tmp_dir = tempfile.mkdtemp()

    # --- old path: dd_wrapper C++ / Cython SampleHandle ---
    output_base = f"{tmp_dir}/old-profile"
    ddup.config(
        env="test_env",
        service="test_service",
        version="1.0",
        tags={},
        max_nframes=64,
        output_filename=output_base,
        use_native_uploader=True,
    )
    ddup.start()

    old_handle = ddup.SampleHandle()
    old_handle.push_frame("hot_loop", "app.py", 0, 100)
    old_handle.push_walltime(1_000_000, 1)
    old_handle.push_threadinfo(1, 100, "MainThread")
    old_handle.flush_sample()

    ddup.upload(ddtrace.tracer, start_ns=0)

    old_pprof_matches = glob.glob(f"{output_base}.*.*.pprof")
    assert len(old_pprof_matches) == 1, old_pprof_matches
    old_prof = pprof_utils.parse_profile(old_pprof_matches[0])

    # --- new path: PyO3 DdProfile/SampleHandle onto libdd-profiling directly ---
    # `SAMPLE_TYPE_ALL` matches the mask dd_wrapper's `ProfilerState` actually
    # uses in production (`profiler_state.hpp`'s `type_mask{SampleType::All}`
    # default): `ddup_config_sample_type` is declared in `_ddup.pyx` but never
    # called, so every real profile is built with every sample type enabled.
    new_profile = _native.DdProfile(_native.SAMPLE_TYPE_ALL, 64)
    new_handle = new_profile.start_sample()
    new_handle.push_frame("hot_loop", "app.py", 0, 100)
    new_handle.push_walltime(1_000_000, 1)
    new_handle.push_threadinfo(1, 100, "MainThread")
    new_profile.add_sample(new_handle)

    buffer, _start_ns, _end_ns = new_profile.serialize(None)
    new_pprof_path = f"{tmp_dir}/new-profile.pprof"
    with open(new_pprof_path, "wb") as f:
        f.write(buffer)
    new_prof = pprof_utils.parse_profile(new_pprof_path)

    # Sample-type *order* determines pprof column indices; a divergence in
    # `setup_samplers`'s ordering would pass a name-keyed value comparison
    # silently, so it's asserted separately, before the full diff.
    old_sample_type_order = [old_prof.string_table[st.type] for st in old_prof.sample_type]
    new_sample_type_order = [new_prof.string_table[st.type] for st in new_prof.sample_type]
    assert old_sample_type_order == new_sample_type_order

    assert dump_samples(old_prof) == dump_samples(new_prof)


@pytest.mark.subprocess()
def test_dualwrite_frame_truncation_parity():
    """dd_wrapper's `Sample::push_frame` drops frames beyond `max_nframes`,
    and `export_sample()` appends one synthetic `<N frame(s) omitted>`
    location for the dropped count (`sample.cpp`). This exercises that both
    paths apply the same cap and synthesize the same marker frame.
    """
    import glob
    import tempfile

    import ddtrace
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.native import _native
    from tests.profiling.collector import pprof_utils

    def dump_samples(prof):
        out = []
        for sample in prof.sample:
            frames = []
            for location_id in sample.location_id:
                location = pprof_utils.get_location_with_id(prof, location_id)
                function = pprof_utils.get_function_with_id(prof, location.line[0].function_id)
                frames.append(
                    (prof.string_table[function.name], prof.string_table[function.filename], location.line[0].line)
                )
            labels = sorted(
                (prof.string_table[label.key], prof.string_table[label.str] if label.str else label.num)
                for label in sample.label
            )
            values = dict(zip([prof.string_table[st.type] for st in prof.sample_type], sample.value))
            out.append({"frames": frames, "labels": labels, "values": values})
        return out

    max_nframes = 4
    num_frames = 10
    tmp_dir = tempfile.mkdtemp()

    output_base = f"{tmp_dir}/old-profile"
    ddup.config(
        env="test_env",
        service="test_service",
        version="1.0",
        tags={},
        max_nframes=max_nframes,
        output_filename=output_base,
        use_native_uploader=True,
    )
    ddup.start()

    old_handle = ddup.SampleHandle()
    for i in range(num_frames):
        old_handle.push_frame(f"frame_{i}", "app.py", 0, i)
    old_handle.push_walltime(1_000_000, 1)
    old_handle.flush_sample()

    ddup.upload(ddtrace.tracer, start_ns=0)
    old_pprof_matches = glob.glob(f"{output_base}.*.*.pprof")
    assert len(old_pprof_matches) == 1, old_pprof_matches
    old_prof = pprof_utils.parse_profile(old_pprof_matches[0])

    new_profile = _native.DdProfile(_native.SAMPLE_TYPE_ALL, max_nframes)
    new_handle = new_profile.start_sample()
    for i in range(num_frames):
        new_handle.push_frame(f"frame_{i}", "app.py", 0, i)
    new_handle.push_walltime(1_000_000, 1)
    new_profile.add_sample(new_handle)

    buffer, _start_ns, _end_ns = new_profile.serialize(None)
    new_pprof_path = f"{tmp_dir}/new-profile.pprof"
    with open(new_pprof_path, "wb") as f:
        f.write(buffer)
    new_prof = pprof_utils.parse_profile(new_pprof_path)

    (old_sample,) = old_prof.sample
    assert len(old_sample.location_id) == max_nframes + 1  # +1 for the omitted-frames marker
    assert dump_samples(old_prof) == dump_samples(new_prof)


@pytest.mark.subprocess()
def test_dualwrite_timeline_parity():
    """`push_monotonic_ns` only takes effect when timeline mode is enabled
    (`ProfilerState::is_timeline_enabled` on the old path / the
    `TIMELINE_ENABLED` module-level flag on the new path). Both compute
    `end_timestamp_ns` from the same monotonic clock reading via an
    independently memoized epoch offset, so exact equality isn't expected,
    but they should land within a generous tolerance of each other since
    both offsets are captured moments apart in this same process.
    """
    import glob
    import tempfile

    import ddtrace
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.internal.native import _native
    from tests.profiling.collector import pprof_utils

    def get_timestamp_ns(prof):
        (sample,) = prof.sample
        for label in sample.label:
            if prof.string_table[label.key] == "end_timestamp_ns":
                return label.num
        raise AssertionError("no end_timestamp_ns label found")

    monotonic_ns = 123_456_789_000
    tmp_dir = tempfile.mkdtemp()

    output_base = f"{tmp_dir}/old-profile"
    ddup.config(
        env="test_env",
        service="test_service",
        version="1.0",
        tags={},
        max_nframes=64,
        output_filename=output_base,
        use_native_uploader=True,
        timeline_enabled=True,
    )
    ddup.start()

    old_handle = ddup.SampleHandle()
    old_handle.push_frame("hot_loop", "app.py", 0, 100)
    old_handle.push_walltime(1_000_000, 1)
    old_handle.push_monotonic_ns(monotonic_ns)
    old_handle.flush_sample()

    ddup.upload(ddtrace.tracer, start_ns=0)
    old_pprof_matches = glob.glob(f"{output_base}.*.*.pprof")
    assert len(old_pprof_matches) == 1, old_pprof_matches
    old_prof = pprof_utils.parse_profile(old_pprof_matches[0])

    _native.set_timeline(True)
    new_profile = _native.DdProfile(_native.SAMPLE_TYPE_ALL, 64)
    new_handle = new_profile.start_sample()
    new_handle.push_frame("hot_loop", "app.py", 0, 100)
    new_handle.push_walltime(1_000_000, 1)
    new_handle.push_monotonic_ns(monotonic_ns)
    new_profile.add_sample(new_handle)

    buffer, _start_ns, _end_ns = new_profile.serialize(None)
    new_pprof_path = f"{tmp_dir}/new-profile.pprof"
    with open(new_pprof_path, "wb") as f:
        f.write(buffer)
    new_prof = pprof_utils.parse_profile(new_pprof_path)

    old_timestamp_ns = get_timestamp_ns(old_prof)
    new_timestamp_ns = get_timestamp_ns(new_prof)
    assert old_timestamp_ns > 0
    assert abs(old_timestamp_ns - new_timestamp_ns) < 1_000_000_000  # within 1s
