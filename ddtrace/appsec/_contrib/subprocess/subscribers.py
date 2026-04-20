from typing import Union

from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._processor import AppSecSpanProcessor
from ddtrace.contrib._events.command import CommandEvents
from ddtrace.contrib._events.command import ProcessCommandEvent
from ddtrace.contrib._events.command import ShellCommandEvent
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.core.subscriber import Subscriber


class AppSecShiSubscriber(Subscriber[ShellCommandEvent]):
    """Subscriber for shell injection (SHI) detection on os.system calls."""

    event_names = (CommandEvents.OS_SYSTEM.value,)

    @classmethod
    def on_event(cls, event_instance: ShellCommandEvent) -> None:
        if not AppSecSpanProcessor.rasp_enabled("shi"):
            return
        if not in_asm_context():
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SHI, False)
            return
        call_waf_callback(
            {EXPLOIT_PREVENTION.ADDRESS.SHI: event_instance.command},
            crop_trace="AppSecShiSubscriber",
            rule_type=EXPLOIT_PREVENTION.TYPE.SHI,
        )
        if blocking_config := get_blocked():
            raise BlockingException(
                blocking_config, EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SHI, event_instance.command
            )


class AppSecCmdiSubscriber(Subscriber[ProcessCommandEvent]):
    """Subscriber for command injection (CMDI) detection on subprocess.Popen calls."""

    event_names = (CommandEvents.SUBPROCESS_POPEN.value,)

    @classmethod
    def on_event(cls, event_instance: ProcessCommandEvent) -> None:
        if not AppSecSpanProcessor.rasp_enabled("cmdi"):
            return
        if not in_asm_context():
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.CMDI, False)
            return
        arg_list: Union[list[str], str] = event_instance.command_args
        call_waf_callback(
            {EXPLOIT_PREVENTION.ADDRESS.CMDI: arg_list if isinstance(arg_list, list) else [arg_list]},
            crop_trace="AppSecCmdiSubscriber",
            rule_type=EXPLOIT_PREVENTION.TYPE.CMDI,
        )
        if blocking_config := get_blocked():
            raise BlockingException(
                blocking_config, EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.CMDI, arg_list
            )
