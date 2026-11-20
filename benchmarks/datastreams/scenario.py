import bm

from ddtrace.internal.datastreams.processor import DataStreamsProcessor
from ddtrace.internal.datastreams.processor import DsmPathwayCodec


PRODUCE_TAGS = ["direction:out", "topic:benchmark-topic", "type:kafka"]
CONSUME_TAGS = ["direction:in", "group:benchmark-group", "topic:benchmark-topic", "type:kafka"]


class DataStreams(bm.Scenario):
    operation: str

    def run(self):
        # Stopped processor with an unreachable agent: nothing is flushed during or after the timed loop.
        processor = DataStreamsProcessor(agent_url="http://127.0.0.1:1", timeout=0.1, retry_attempts=1)
        processor.stop()

        produced_headers = {}
        DsmPathwayCodec.encode(processor.set_checkpoint(PRODUCE_TAGS, payload_size=512), produced_headers)
        ctx = DsmPathwayCodec.decode(produced_headers, processor)

        if self.operation == "produce":

            def _(loops):
                for _ in range(loops):
                    headers = {}
                    DsmPathwayCodec.encode(processor.set_checkpoint(PRODUCE_TAGS, payload_size=512), headers)

        elif self.operation == "consume":

            def _(loops):
                for _ in range(loops):
                    DsmPathwayCodec.decode(produced_headers, processor).set_checkpoint(CONSUME_TAGS, payload_size=512)

        elif self.operation == "encode":

            def _(loops):
                for _ in range(loops):
                    DsmPathwayCodec.encode(ctx, {})

        elif self.operation == "decode":

            def _(loops):
                for _ in range(loops):
                    DsmPathwayCodec.decode(produced_headers, processor)

        else:
            raise ValueError("unknown operation: %s" % self.operation)

        yield _

        # Drop recorded stats so the exit-time flush has nothing to send.
        processor._buckets.clear()
