from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.contrib._events.subprocess import SubprocessCommandEvent
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import Subscriber


class AppSecSubprocessCommandSubscriber(Subscriber):
    event_names = (SubprocessCommandEvent.event_name,)

    @classmethod
    def on_event(cls, event: SubprocessCommandEvent) -> None:
        if event.shell:
            cls._on_shell_command(event)
        else:
            cls._on_exec_command(event)

    @staticmethod
    def _on_shell_command(event: SubprocessCommandEvent) -> None:
        if get_rasp_capability("shi"):
            if in_asm_context():
                result = call_waf_callback(
                    {EXPLOIT_PREVENTION.ADDRESS.SHI: event.command},
                    rule_type=EXPLOIT_PREVENTION.TYPE.SHI,
                )
                if result and _must_block(result.actions):
                    raise BlockingException(
                        get_blocked(),
                        EXPLOIT_PREVENTION.BLOCKING,
                        EXPLOIT_PREVENTION.TYPE.SHI,
                        event.command,
                    )
            else:
                report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SHI, False)

    @staticmethod
    def _on_exec_command(event: SubprocessCommandEvent) -> None:
        if get_rasp_capability("cmdi"):
            if in_asm_context():
                waf_command = [event.command] if isinstance(event.command, (str, bytes)) else event.command
                result = call_waf_callback(
                    {EXPLOIT_PREVENTION.ADDRESS.CMDI: waf_command},
                    rule_type=EXPLOIT_PREVENTION.TYPE.CMDI,
                )
                if result and _must_block(result.actions):
                    raise BlockingException(
                        get_blocked(),
                        EXPLOIT_PREVENTION.BLOCKING,
                        EXPLOIT_PREVENTION.TYPE.CMDI,
                        event.command,
                    )
            else:
                report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.CMDI, False)
