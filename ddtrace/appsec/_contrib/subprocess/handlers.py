from typing import Union

from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.contrib.internal.subprocess.constants import COMMAND_EVENT
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException


CommandPart = Union[str, bytes]
Command = Union[CommandPart, list[CommandPart], tuple[CommandPart, ...]]


def listen() -> None:
    core.on(COMMAND_EVENT, on_subprocess_command)


def on_subprocess_command(command: Command, shell: bool) -> None:
    if shell:
        _on_shell_command(command)
    else:
        _on_exec_command(command)


def _on_shell_command(command: Command) -> None:
    if get_rasp_capability("shi"):
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_asm_context
        except ImportError:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SHI, True)
            return

        if in_asm_context():
            result = call_waf_callback(
                {EXPLOIT_PREVENTION.ADDRESS.SHI: command},
                crop_trace="wrapped_system_5542593D237084A7",
                rule_type=EXPLOIT_PREVENTION.TYPE.SHI,
            )
            if result and _must_block(result.actions):
                raise BlockingException(
                    get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SHI, command
                )
        else:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SHI, False)


def _on_exec_command(command: Command) -> None:
    if get_rasp_capability("cmdi"):
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_asm_context
        except ImportError:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.CMDI, True)
            return

        if in_asm_context():
            waf_command: list[Union[CommandPart, tuple[CommandPart, ...]]] = []
            if isinstance(command, list):
                waf_command.extend(command)
            else:
                waf_command.append(command)
            result = call_waf_callback(
                {EXPLOIT_PREVENTION.ADDRESS.CMDI: waf_command},
                crop_trace="popen_FD233052260D8B4D",
                rule_type=EXPLOIT_PREVENTION.TYPE.CMDI,
            )
            if result and _must_block(result.actions):
                raise BlockingException(
                    get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.CMDI, command
                )
        else:
            report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.CMDI, False)
