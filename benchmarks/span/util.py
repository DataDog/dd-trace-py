from random import choice
from random import randint
import string

from ddtrace import Span


def gen_tags(ntags=50, ltags=30):
    tag_values = [rands(size=ltags) for _ in range(ntags)]
    tag_keys = [rands(size=ltags) for _ in range(ntags)]
    tags = {tag_keys[i]: tag_values[i] for i in range(len(tag_keys))}
    return tags


def gen_metrics(nmetrics=50, lmetrics=30):
    metric_keys = [rands(size=lmetrics) for _ in range(nmetrics)]
    metric_values = [random_w_n_digits(lmetrics) for _ in range(nmetrics)]
    tags = {metric_keys[i]: metric_values[i] for i in range(len(metric_keys))}
    return tags


def gen_spans(nspans=1, trace_id=None):
    spans = []
    for _ in range(0, nspans):
        spans.append(Span(None, "test.op", resource="resource", service="service", trace_id=trace_id))
    return spans


def random_w_n_digits(lmetrics):
    range_start = 10 ** (lmetrics - 1)
    range_end = (10 ** lmetrics) - 1
    return randint(range_start, range_end)


def rands(size=6, chars=string.ascii_letters + string.digits):
    return "".join(choice(chars) for _ in range(size))
