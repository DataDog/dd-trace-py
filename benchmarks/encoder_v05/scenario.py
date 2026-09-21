from typing import Callable
from typing import Generator

import bm

from ddtrace._trace.span import Span
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal.encoding import MsgpackEncoderV05


class MsgpackEncoderScenario(bm.Scenario):
    workload: str

    @staticmethod
    def _span(index: int, tags: int, metrics: int) -> Span:
        span = Span(
            f"flask.span.{index}",
            service="flask",
            resource=f"GET /resource/{index}",
            span_type="web",
        )
        for tag in range(tags):
            span._set_attribute(f"tag.{tag}", f"value-{index}-{tag:02d}")
        for metric in range(metrics):
            span._set_attribute(f"metric.{metric}", float(index + metric))
        span.finish()
        return span

    def _trace(self) -> list[Span]:
        if self.workload == "simple_one_span":
            return [self._span(0, tags=0, metrics=0)]
        if self.workload == "flask_trace":
            attribute_counts = ((27, 5), (15, 0)) + ((13, 0),) * 7
            return [
                self._span(index, tags=tags, metrics=metrics) for index, (tags, metrics) in enumerate(attribute_counts)
            ]
        raise ValueError(f"unknown encoder workload: {self.workload}")

    def run(self) -> Generator[Callable[[int], None], None, None]:
        encoder = MsgpackEncoderV05(8 << 20, 8 << 20)
        trace = self._trace()

        def encode(loops: int) -> None:
            for _ in range(loops):
                try:
                    encoder.put(trace)
                except BufferFull:
                    encoder.encode()
                    encoder.put(trace)

        yield encode
