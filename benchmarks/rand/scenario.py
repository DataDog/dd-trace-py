import os

import bm


class Rand(bm.Scenario):
    func: str  # "rand64bits" or "rand128bits"
    snapstart: bool  # emulate AWS Lambda SnapStart (AWS_LAMBDA_INITIALIZATION_TYPE=snap-start)

    def run(self):
        if self.snapstart:
            # The native module reads AWS_LAMBDA_INITIALIZATION_TYPE once at
            # import, so it must be set before ddtrace.internal.native loads.
            os.environ["AWS_LAMBDA_INITIALIZATION_TYPE"] = "snap-start"

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
