from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.filesystem.events import FileOpenEvent
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import Subscriber


class AppSecFileOpenSubscriber(Subscriber):
    event_names = (FileOpenEvent.event_name,)

    @classmethod
    def on_event(cls, event: FileOpenEvent) -> None:
        if not get_rasp_capability("lfi"):
            return

        if not in_asm_context():
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.LFI, False)
            return

        result = call_waf_callback(
            {EXPLOIT_PREVENTION.ADDRESS.LFI: event.filename},
            crop_trace="on_event",
            rule_type=EXPLOIT_PREVENTION.TYPE.LFI,
        )
        if result is None or not _must_block(result.actions):
            return

        raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.LFI, event.filename)
