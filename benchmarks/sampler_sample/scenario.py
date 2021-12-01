import json

import bm

import ddtrace
from ddtrace.sampler import DatadogSampler
from ddtrace.span import Span


class DatadogSamplerSample(bm.Scenario):
    iterations = bm.var(type=int)
    default_sample_rate = bm.var(type=float)
    sampling_rules = bm.var(type=str)
    rates_by_service = bm.var(type=str)
    rate_limit = bm.var(type=float)
    service_name = bm.var(type=str)
    operation_name = bm.var(type=str)

    def get_rates_by_service(self):
        if not self.rates_by_service:
            return None

        return json.loads(self.rates_by_service)

    def create_sampler(self):
        default_sample_rate = self.default_sample_rate if self.default_sample_rate >= 0 else None
        rate_limit = self.rate_limit if self.rate_limit >= 0 else None
        sampler = DatadogSampler(rules=None, default_sample_rate=default_sample_rate, rate_limit=rate_limit)
        if self.sampling_rules:
            sampler.rules = sampler._parse_rules_from_env_variable(self.sampling_rules)
        rates_by_service = self.get_rates_by_service()
        if rates_by_service:
            sampler.update_rate_by_service_sample_rates(rates_by_service)
        return sampler

    def create_spans(self):
        # Generate an evenly spaced set of trace ids from our minimum to maximum allowed values
        # DEV: Our sampling logic is based on trace id cut offs (e.g. 50% says < 2**32 is kept, > 2**32 is not)
        #      Evenly spacing the numbers means we should match roughly the supplied sample rate in success/pass
        lower = 1
        upper = 2 ** 63
        trace_ids = [int(lower + x * (upper - lower) / self.iterations) for x in range(self.iterations)]

        # The span ids don't matter, but let's make them unique anyways
        span_ids = list(range(1, self.iterations + 1))

        return [
            Span(
                tracer=ddtrace.tracer,
                service=self.service_name,
                name=self.operation_name,
                trace_id=trace_id,
                span_id=span_id,
            )
            for trace_id, span_id in zip(trace_ids, span_ids)
        ]

    def run(self):
        sampler = self.create_sampler()
        spans = self.create_spans()

        def _(loops):
            for _ in range(loops):
                for span in spans:
                    sampler.sample(span)

        yield _
