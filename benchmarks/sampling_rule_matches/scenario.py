import itertools
import random
import string

import bm

from ddtrace._trace.span import Span
from ddtrace.sampling_rule import SamplingRule


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
    num_iterations: int
    num_services: int
    num_operations: int
    num_resources: int
    num_tags: int

    def run(self):
        # Generate random service and operation names for the counts we requested
        services = [rands() for _ in range(self.num_services)]
        operation_names = [rands() for _ in range(self.num_operations)]
        resource_names = [rands() for _ in range(self.num_resources)]
        tag_names = [rands() for _ in range(self.num_tags)]

        # Generate all possible permutations of service, operation name, resource, and tag
        spans = []
        for service, name, resource, tag in itertools.product(services, operation_names, resource_names, tag_names):
            span = Span(service=service, name=name, resource=resource)
            span.set_tag(tag, tag)
            spans.append(span)

        # Create a single rule to use for all matches
        # Pick a random service/operation name/resource/tag to use for the rule
        tag = random.choice(tag_names)
        rule = SamplingRule(
            service=random.choice(services),
            name=random.choice(operation_names),
            resource=random.choice(resource_names),
            tags={tag: tag},
            sample_rate=1.0,
        )

        def _(loops):
            for _ in range(loops):
                for span in iter_n(spans, n=self.num_iterations):
                    rule.matches(span)

        yield _
