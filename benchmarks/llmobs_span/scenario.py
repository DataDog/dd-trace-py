from bm import Scenario
import bm.utils as utils

from ddtrace.constants import SPAN_KIND
from ddtrace.llmobs import LLMObs
from ddtrace.trace import tracer


class LLMObsSpan(Scenario):
    """Cost of finishing APM spans in a process with LLMObs enabled.

    With LLMObs enabled every finished span goes through LLMObs' span-finish listener, not only LLMObs
    spans, so plain and client spans are measured with and without an enclosing tool span.
    """

    llmobs_enabled: bool
    nspans: int
    client: bool
    in_tool: bool

    def run(self):
        if self.llmobs_enabled:
            LLMObs.enable(ml_app="benchmark", _tracer=tracer, agentless_enabled=False)
            # Drop LLMObs events: nothing is listening for them, and an empty buffer never flushes.
            LLMObs._instance._llmobs_span_writer.enqueue = lambda *args, **kwargs: None
        utils.drop_traces(tracer)
        utils.drop_telemetry_events()

        nspans = self.nspans
        client = self.client
        in_tool = self.in_tool

        def spans():
            for i in range(nspans):
                with tracer.trace("http.request", span_type="http") as span:
                    if client:
                        span.set_tag(SPAN_KIND, "client")
                        span.set_tag("component", "httpx")
                        span.set_tag("http.method", "GET")
                        span.set_tag("http.url", "https://api.example.com/v1/orders/%d" % (i % 10))
                        span.set_tag("http.status_code", "200")

        def _(loops):
            for _ in range(loops):
                if in_tool:
                    with LLMObs.tool("execute_code"):
                        spans()
                else:
                    with tracer.trace("root"):
                        spans()

        yield _
