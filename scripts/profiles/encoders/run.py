import sys

from ddtrace.internal.encoding import MSGPACK_ENCODERS as ENCODERS
from tests.tracer.test_encoders import gen_trace


encoder = ENCODERS[sys.argv[1]](8 << 20, 8 << 20)


trace = gen_trace(nspans=1000)

for _ in range(200):
    encoder.put(trace)
    encoder.encode()
