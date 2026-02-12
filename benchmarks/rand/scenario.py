import bm


class Rand(bm.Scenario):
    func: str  # "rand64bits" or "rand128bits"

    def run(self):
        try:
            from ddtrace.internal.native import rand64bits

            rand64 = rand64bits
        except ImportError:
            from ddtrace.internal._rand import rand64bits

            rand64 = rand64bits

        try:
            from ddtrace.internal.native import generate_128bit_trace_id

            rand128 = generate_128bit_trace_id
        except ImportError:
            from ddtrace.internal._rand import rand128bits

            rand128 = rand128bits

        funcs = {
            "rand64bits": rand64,
            "rand128bits": rand128,
        }
        f = funcs[self.func]

        def _(loops):
            for _ in range(loops):
                f()

        yield _
