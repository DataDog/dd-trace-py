import itertools
import random
import string

import bm

from ddtrace import Span
from ddtrace.sampler import SamplingRule


def rands(size=6, chars=string.ascii_uppercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))


def iter_n(items, n):
    """Return back n results from items, even if len(items) < n"""
    i = 0
    t = len(items)
    for _ in range(n):
        yield items[i]
        i += 1
        if i >= t:
            i = 0


class SamplingRules(bm.Scenario):
    num_iterations = bm.var(type=int)
    num_services = bm.var(type=int)
    num_operations = bm.var(type=int)

    def run(self):
        # Generate random service and operation names for the counts we requested
        services = [rands() for _ in range(self.num_services)]
        operation_names = [rands() for _ in range(self.num_operations)]

        # Generate all possible permutations of service and operation names
        spans = [
            Span(tracer=None, service=service, name=name)
            for service, name in itertools.product(services, operation_names)
        ]

        # Create a single rule to use for all matches
        # Pick a random service/operation name
        rule = SamplingRule(
            service=random.choice(services),
            name=random.choice(operation_names),
            sample_rate=1.0,
        )

        def _(loops):
            for _ in range(loops):
                for span in iter_n(spans, n=self.num_iterations):
                    rule.matches(span)

        yield _
