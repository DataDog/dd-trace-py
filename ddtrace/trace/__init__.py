from ddtrace._trace.context import Context
from ddtrace._trace.filters import TraceFilter
from ddtrace._trace.provider import BaseContextProvider
from ddtrace._trace.span import Span
from ddtrace._trace.tracer import Tracer
from ddtrace.internal import core
from ddtrace.internal._runtime_id import maybe_refresh_identity
from ddtrace.internal._runtime_id import on_runtime_identity_refresh
from ddtrace.internal.constants import WEB_REQUEST_STARTING_EVENT
from ddtrace.internal.serverless import in_aws_lambda_microvm


# a global tracer instance with integration settings
tracer = Tracer()
core.root.set_item("tracer", tracer)
# The single owner of the MicroVM /run wiring. Both registrations are idempotent.
if in_aws_lambda_microvm():
    on_runtime_identity_refresh(tracer._refresh_runtime_identity)
    core.on(WEB_REQUEST_STARTING_EVENT, maybe_refresh_identity)


def _on_asm_tracer_reconfigure(appsec_enabled: bool, origin: str) -> None:
    tracer.configure(appsec_enabled=appsec_enabled, appsec_enabled_origin=origin)


core.on("asm.tracer.reconfigure", _on_asm_tracer_reconfigure)


__all__ = [
    "BaseContextProvider",
    "Context",
    "TraceFilter",
    "Tracer",
    "Span",
    "tracer",
]
