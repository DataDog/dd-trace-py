from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.contrib._events.filesystem import FileOpenEvent
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import Subscriber


class AppSecFileOpenSubscriber(Subscriber):
    auto_register = False
    event_names = (FileOpenEvent.event_name,)

    @classmethod
    def on_event(cls, event: FileOpenEvent) -> None:
        if not get_rasp_capability("lfi"):
            return
        filename = event.filename
        if not filename:
            return

        result = call_waf_callback(
            {EXPLOIT_PREVENTION.ADDRESS.LFI: filename},
            crop_trace=event.stack_trace_boundary or "on_event",
            rule_type=EXPLOIT_PREVENTION.TYPE.LFI,
        )
        if result and _must_block(result.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.LFI, filename)
