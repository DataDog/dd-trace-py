import json
import os
import random
import string

import yaml

from ddtrace.internal import _rand
from ddtrace.internal.encoding import Encoder
from ddtrace.span import Span
from ddtrace.utils.attrdict import AttrDict


HERE = os.path.dirname(os.path.abspath(__file__))


def read_config(path):
    with open(path, "r") as fp:
        return yaml.load(fp, Loader=yaml.FullLoader)


try:
    # the introduction of the buffered encoder changed the internal api
    # see https://github.com/DataDog/dd-trace-py/pull/2422
    from ddtrace.internal._encoding import BufferedEncoder  # noqa: F401

    def init_encoder(max_size=8 << 20, max_item_size=8 << 20):
        return Encoder(max_size, max_item_size)


except ImportError:

    def init_encoder():
        return Encoder()


def _rands(size=6, chars=string.ascii_uppercase + string.digits):
    return "".join(random.choice(chars) for _ in range(size))


def _random_values(k, size):
    return list(set([_rands(size=size) for _ in range(k)]))


def gen_traces(config):
    traces = []

    # choose from a set of randomly generated span attributes
    span_names = _random_values(256, 16)
    resources = _random_values(256, 16)
    services = _random_values(16, 16)
    tag_keys = _random_values(config.ntags, 16)
    metric_keys = _random_values(config.nmetrics, 16)
    dd_origin_values = ["synthetics", "ciapp-test"]

    for _ in range(config.ntraces):
        trace = []
        for i in range(0, config.nspans):
            # first span is root so has no parent otherwise parent is root span
            parent_id = trace[0]["span_id"] if i > 0 else None
            span_name = random.choice(span_names)
            resource = random.choice(resources)
            service = random.choice(services)
            span = dict(
                name=span_name,
                resource=resource,
                service=service,
                parent_id=parent_id,
                span_id=_rand.rand64bits(),
                trace_id=_rand.rand64bits(),
            )

            if i == 0 and config.dd_origin:
                # Since we're not using the tracer API, a span's context isn't automatically propagated
                # to its children. The encoder only checks the root span's context in a trace for dd_origin, so
                # here we need to add dd_origin to the root span's context.
                span["dd_origin"] = random.choice(dd_origin_values)

            if config.ntags > 0:
                span["meta"] = dict(zip(tag_keys, [_rands(size=config.ltags) for _ in range(config.ntags)]))

            if config.nmetrics > 0:
                span["metrics"] = dict(
                    zip(
                        metric_keys,
                        [random.randint(0, 2 ** 16) for _ in range(config.nmetrics)],
                    )
                )

            trace.append(span)

        traces.append(trace)

    return traces


def serialize_config(config):
    return "_".join(sorted(("-".join((k, str(v))) for k, v in config.items())))


def load_traces(config):
    config_file = os.path.join(HERE, serialize_config(config)) + ".json"

    traces = []

    with open(config_file) as f:
        data = json.load(f)
        for t in data:
            trace = []
            traces.append(trace)
            for s in t:
                with Span(
                    tracer=None,
                    name=s["name"],
                    resource=s["resource"],
                    service=s["service"],
                    parent_id=s["parent_id"],
                    span_id=s["span_id"],
                    trace_id=s["trace_id"],
                ) as span:
                    trace.append(span)

                    if "dd_origin" in s:
                        span.context.dd_origin = s["dd_origin"]
                    if "meta" in s:
                        span.set_tags(s["meta"])
                    if "metrics" in s:
                        span.set_metrics(s["metrics"])

    return traces


def gen_data():
    config = read_config(os.path.join(HERE, "config.yaml"))

    for name, _ in config.items():
        _["name"] = name
        dest = os.path.join(HERE, serialize_config(_)) + ".json"
        if os.path.isfile(dest):
            # data was already generated
            continue

        with open(dest, "w") as f:
            c = AttrDict(_)
            t = gen_traces(c)
            json.dump(t, f)
