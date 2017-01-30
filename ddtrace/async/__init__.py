from .tracer import AsyncTracer


# a global async tracer instance
# TODO: we may don't need this separated approach
tracer = AsyncTracer()
