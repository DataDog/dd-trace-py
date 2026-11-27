from functools import partial
import random
import string
from typing import Any

from ddtrace import __version__ as ddtrace_version
from ddtrace._trace.span import Span
from ddtrace.internal.encoding import MSGPACK_ENCODERS


_Span: Any = Span

# DEV: 1.x dropped tracer positional argument
if ddtrace_version.split(".")[0] == "0":
    _Span = partial(_Span, None)

try:
    # the introduction of the buffered encoder changed the internal api
    # see https://github.com/DataDog/dd-trace-py/pull/2422
    from ddtrace.internal._encoding import BufferedEncoder  # noqa: F401

    def init_encoder(encoding: str, max_size: int = 8 << 20, max_item_size: int = 8 << 20) -> Any:
        return MSGPACK_ENCODERS[encoding](max_size, max_item_size)

except ImportError:

    def init_encoder(encoding: str, max_size: int = 8 << 20, max_item_size: int = 8 << 20) -> Any:
        # Only reached on ddtrace versions whose encoders take no size arguments.
        return MSGPACK_ENCODERS[encoding]()  # type: ignore[call-arg]


def _rands(size: int = 6, chars: str = string.ascii_uppercase + string.digits) -> str:
    return "".join(random.choice(chars) for _ in range(size))  # nosec B311 - benchmark data


def _random_values(k: int, size: int) -> "list[str]":
    return list(dict.fromkeys([_rands(size=size) for _ in range(k)]))


def gen_traces(config: Any) -> "list[list[Span]]":
    random.seed(1)

    traces: list[list[Span]] = []

    # choose from a set of randomly generated span attributes
    span_names = _random_values(256, 16)
    resources = _random_values(256, 16)
    services = _random_values(16, 16)
    tag_keys = _random_values(config.ntags, 16)
    metric_keys = _random_values(config.nmetrics, 16)
    dd_origin_values = ["synthetics", "ciapp-test"]
    # meta_struct values go through the generic msgpack packer rather than the hand-rolled encoder,
    # so the shape mirrors what LLMObs and AppSec attach: nested dicts of scalars and short lists.
    struct_value = {
        "item-%d" % i: {"v": _rands(size=config.lmetastructs), "seq": [_rands(size=8), str(i)]}
        for i in range(config.nmetastructs)
    }

    for _ in range(config.ntraces):
        trace: list[Span] = []
        for i in range(0, config.nspans):
            # first span is root so has no parent otherwise parent is root span
            parent_id = trace[0].span_id if i > 0 else None
            span_name = random.choice(span_names)  # nosec B311 - benchmark data
            resource = random.choice(resources)  # nosec B311 - benchmark data
            service = random.choice(services)  # nosec B311 - benchmark data
            with _Span(span_name, resource=resource, service=service, parent_id=parent_id) as span:
                if i == 0 and config.dd_origin:
                    # Since we're not using the tracer API, a span's context isn't automatically propagated
                    # to its children. The encoder only checks the root span's context in a trace for dd_origin, so
                    # here we need to add dd_origin to the root span's context.
                    span.context.dd_origin = random.choice(dd_origin_values)  # nosec B311 - benchmark data
                if config.ntags > 0:
                    span.set_tags(dict(zip(tag_keys, [_rands(size=config.ltags) for _ in range(config.ntags)])))
                if config.nmetastructs > 0:
                    span._set_struct_tag("meta_struct", struct_value)
                if config.nmetrics > 0:
                    span.set_metrics(
                        dict(
                            zip(
                                metric_keys,
                                [random.randint(0, 2**16) for _ in range(config.nmetrics)],  # nosec B311
                            )
                        )
                    )
                trace.append(span)
        traces.append(trace)
    return traces
