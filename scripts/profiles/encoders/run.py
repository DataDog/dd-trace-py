import sys

from ddtrace.internal.encoding import Encoder
from ddtrace.internal.encoding import MsgpackEncoderV03
from ddtrace.internal.encoding import MsgpackEncoderV05
from tests.tracer.test_encoders import gen_trace


ENCODERS = {
    "v0.3": MsgpackEncoderV03,
    "v0.5": MsgpackEncoderV05,
}

try:
    encoder = ENCODERS.get(sys.argv[1], Encoder)(8 << 20, 8 << 20)
except IndexError:
    encoder = Encoder(8 << 20, 8 << 20)


trace = gen_trace(nspans=1000)

for i in range(200):
    encoder.put(trace)
    encoder.encode()
