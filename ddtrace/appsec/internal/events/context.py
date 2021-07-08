from typing import List
from typing import Optional
from typing import Union

import attr
from sq_native import get_lib_version

from ddtrace import __version__
from ddtrace import tracer as ddtracer


LIB_VERSION = get_lib_version()


@attr.s(frozen=True)
class ServiceStackItem(object):
    name = attr.ib(type=str)
    environment = attr.ib(type=Optional[str])
    version = attr.ib(type=Optional[str])
    when = attr.ib(type=str)


@attr.s(frozen=True)
class ServiceStack_0_1_0(object):
    services = attr.ib(type=List[ServiceStackItem])
    context_version = attr.ib(default="0.1.0")


@attr.s(frozen=True)
class Tracer_0_1_0(object):
    runtime_type = attr.ib(type=str)
    runtime_version = attr.ib(type=str)
    lib_version = attr.ib(type=str)
    context_version = attr.ib(default="0.1.0")


@attr.s(frozen=True)
class Trace_0_1_0(object):
    id = attr.ib(type=int)
    context_version = attr.ib(default="0.1.0")


@attr.s(frozen=True)
class Span_0_1_0(object):
    id = attr.ib(type=Union[int, str])
    context_version = attr.ib(default="0.1.0")


@attr.s(frozen=True)
class Service_0_1_0(object):
    name = attr.ib(type=str)
    environment = attr.ib(type=Optional[str], default=None)
    version = attr.ib(type=Optional[str], default=None)
    context_version = attr.ib(default="0.1.0")


@attr.s(frozen=True)
class Actor_0_1_0(object):
    ip = attr.ib(type=str)
    identifiers = attr.ib(type=Optional[dict], default=None)
    _id = attr.ib(type=Optional[str], default=None)
    context_version = attr.ib(default="0.1.0")


@attr.s(frozen=True)
class RequiredContext_0_1_0(object):
    service_stack = attr.ib(type=ServiceStack_0_1_0)
    tracer = attr.ib(type=Tracer_0_1_0)
    trace = attr.ib(type=Trace_0_1_0)
    span = attr.ib(type=Span_0_1_0)
    service = attr.ib(type=Service_0_1_0)
    actor = attr.ib(type=Actor_0_1_0)


def get_required_context(
    actor_ip=None,  # type: Optional[str]
    service=None,  # type: Optional[str]
    span_id=None,  # type: Optional[int]
    trace_id=None,  # type: Optional[int]
    tracer=ddtracer
):
    # type: (...) -> RequiredContext_0_1_0
    ddcontext = tracer.current_trace_context()
    if ddcontext is not None:
        trace_id = ddcontext.trace_id
        span_id = ddcontext.span_id
    if trace_id is None:
        trace_id = 0
    if span_id is None:
        span_id = 0

    root_span = tracer.current_root_span()
    if root_span is not None:
        service = root_span.service
    if service is None:
        # DEV service should not be mandatory
        service = ""

    if actor_ip is None:
        # DEV actor should not be mandatory
        actor_ip = "0.0.0.0"

    return RequiredContext_0_1_0(
        service_stack=ServiceStack_0_1_0([]),
        tracer=Tracer_0_1_0(
            runtime_type="python",
            runtime_version=__version__,
            lib_version=LIB_VERSION,
        ),
        trace=Trace_0_1_0(id=trace_id),
        span=Span_0_1_0(id=span_id),
        service=Service_0_1_0(name=service),
        actor=Actor_0_1_0(ip=actor_ip),
    )
