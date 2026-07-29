from typing import Union

from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.appsec._rasp import must_block
import ddtrace.contrib.internal.subprocess.patch as subprocess_patch
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog


log = get_logger(__name__)

_RASP_SYSTEM = "rasp_os.system"
_RASP_POPEN = "rasp_Popen"
_is_hook_registered = False


def _patch_subprocess(_: object) -> None:
    subprocess_patch.patch()
    subprocess_patch.add_str_callback(_RASP_SYSTEM, wrapped_system)
    subprocess_patch.add_lst_callback(_RASP_POPEN, wrapped_popen)
    log.debug("Patching AppSec subprocess callbacks")


def patch() -> None:
    global _is_hook_registered
    if _is_hook_registered:
        return
    ModuleWatchdog.register_module_hook("subprocess", _patch_subprocess)
    _is_hook_registered = True


def unpatch() -> None:
    global _is_hook_registered
    subprocess_patch.unpatch()
    subprocess_patch.del_str_callback(_RASP_SYSTEM)
    subprocess_patch.del_lst_callback(_RASP_POPEN)
    if _is_hook_registered:
        ModuleWatchdog.unregister_module_hook("subprocess", _patch_subprocess)
        _is_hook_registered = False


def wrapped_system(command: Union[str, bytes]) -> None:
    if not get_rasp_capability("shi"):
        return
    if in_asm_context():
        result = call_waf_callback(
            {EXPLOIT_PREVENTION.ADDRESS.SHI: command},
            crop_trace="wrapped_system_5542593D237084A7",
            rule_type=EXPLOIT_PREVENTION.TYPE.SHI,
        )
        if result and must_block(result.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SHI, command)
    else:
        report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SHI, False)


def wrapped_popen(arguments: Union[list[str], str, bytes]) -> None:
    if not get_rasp_capability("cmdi"):
        return
    if in_asm_context():
        command: list[Union[str, bytes]] = [*arguments] if isinstance(arguments, list) else [arguments]
        result = call_waf_callback(
            {EXPLOIT_PREVENTION.ADDRESS.CMDI: command},
            crop_trace="popen_FD233052260D8B4D",
            rule_type=EXPLOIT_PREVENTION.TYPE.CMDI,
        )
        if result and must_block(result.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.CMDI, arguments)
    else:
        report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.CMDI, False)
