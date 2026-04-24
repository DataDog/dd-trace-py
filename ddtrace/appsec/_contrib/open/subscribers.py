from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._processor import AppSecSpanProcessor
from ddtrace.contrib._events.file_io import FileOpenEvent
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import Subscriber


class AppSecLfiSubscriber(Subscriber[FileOpenEvent]):
    event_names = (FileOpenEvent.event_name,)

    @classmethod
    def on_event(cls, event_instance: FileOpenEvent) -> None:
        if not AppSecSpanProcessor.rasp_enabled("lfi"):
            return
        if not in_asm_context():
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.LFI, False)
            return
        call_waf_callback(
            {EXPLOIT_PREVENTION.ADDRESS.LFI: event_instance.filename},
            crop_trace="AppSecLfiSubscriber",
            rule_type=EXPLOIT_PREVENTION.TYPE.LFI,
        )
        if blocking_config := get_blocked():
            raise BlockingException(
                blocking_config, EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.LFI, event_instance.filename
            )
