import random
import string

from ddtrace.span import Span


def rands(size=6, chars=string.ascii_uppercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))


# set of randomly generated span attributes to be used in traces
SPAN_NAMES = [rands(size=16) for _ in range(256)]
RESOURCES = [rands(size=16) for _ in range(256)]
SERVICES = [rands(size=16) for _ in range(16)]
TAG_KEYS = [rands(size=16) for _ in range(256)]
METRIC_KEYS = [rands(size=16) for _ in range(256)]


def gen_traces(ntraces=1, nspans=1, ntags=0, ltags=0, nmetrics=0):
    traces = []
    for _ in range(ntraces):
        trace = []
        tag_values = [rands(size=ltags) for _ in range(ntags)]
        for i in range(0, nspans):
            # first span is root so has no parent otherwise parent is root span
            parent_id = trace[0].span_id if i > 0 else None
            span_name = random.sample(SPAN_NAMES, 1)[0]
            resource = random.sample(RESOURCES, 1)[0]
            service = random.sample(SERVICES, 1)[0]
            tag_keys = random.sample(TAG_KEYS, ntags)
            with Span(None, span_name, resource=resource, service=service, parent_id=parent_id) as span:
                if ntags > 0:
                    span.set_tags(dict(zip(random.sample(TAG_KEYS, ntags), random.sample(tag_values, ntags))))
                if nmetrics > 0:
                    span.set_metrics(
                        dict(
                            zip(
                                random.sample(METRIC_KEYS, nmetrics),
                                [random.randint(0, 2 ** 16) for _ in range(nmetrics)],
                            )
                        )
                    )
                trace.append(span)
        traces.append(trace)
    return traces
