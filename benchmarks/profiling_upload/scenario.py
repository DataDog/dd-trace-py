"""Benchmarks the profiler's export/upload interval, old C++ Uploader vs. the PyO3
ProfileUploaderPy pilot (item 6 of PYO3_PRODUCTION_READINESS_REVIEW.md: does the extra
buffer round-trip -- Rust -> C ABI `std::string` -> Python `bytes` -> Rust `Vec<u8>` -- that
`use_native_uploader=True` introduces show up against the cost of an export interval as a
whole?

Both variants configure `output_filename` (see `Uploader::upload_unlocked` /
`ProfileUploaderPy::send_blocking` in dd-trace-py), which makes both paths dump the encoded
pprof to a local file instead of doing a real HTTP send. This isolates serialization + the
extra copies from network variance, but it also means the comparison does *not* include the
one thing that would normally dwarf a few buffer copies in production: the HTTP request
itself. Read a small delta here as "the copies are cheap relative to serialization", not as
"the copies are cheap relative to a real upload" -- the latter needs a real agent/endpoint,
which this harness deliberately avoids depending on.

Sample construction (`push_frame`/`push_walltime`/`flush_sample`) is identical between the
two variants -- both go through the same C++ `ddup_start_sample`/`push_*`/`ddup_flush_sample`
machinery regardless of `use_native_uploader` -- so any timing delta between variants at a
given `nsamples` is attributable to serialize+send, not to sample collection.
"""

import os
import shutil
import tempfile

import bm


class ProfilingUpload(bm.Scenario):
    use_native_uploader: bool
    nsamples: int

    def run(self):
        from ddtrace.internal.datadog.profiling import ddup

        tmp_dir = tempfile.mkdtemp()
        output_base = os.path.join(tmp_dir, "profile")

        ddup.config(
            service="benchmark",
            env="benchmark",
            version="1.0",
            tags={},
            max_nframes=64,
            output_filename=output_base,
            use_native_uploader=self.use_native_uploader,
        )
        ddup.start()

        import ddtrace

        tracer = ddtrace.tracer

        def _fill_one_profile():
            for i in range(self.nsamples):
                handle = ddup.SampleHandle()
                handle.push_frame("hot_loop", "app.py", 0, i)
                handle.push_walltime(1_000_000, 1)
                handle.push_threadinfo(1, 100, "MainThread")
                handle.flush_sample()

        def _(loops):
            for _ in range(loops):
                _fill_one_profile()
                ddup.upload(tracer, start_ns=0)

        yield _

        shutil.rmtree(tmp_dir, ignore_errors=True)
