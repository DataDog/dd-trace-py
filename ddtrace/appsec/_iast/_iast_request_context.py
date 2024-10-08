import sys
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)

# Stopgap module for providing ASM context for the blocking features wrapping some contextvars.

if sys.version_info >= (3, 8):
    from typing import Literal  # noqa:F401
else:
    from typing_extensions import Literal  # noqa:F401

_IAST_CONTEXT: Literal["_iast_env"] = "_iast_env"


class IASTEnvironment:
    """
    an object of this class contains all asm data (waf and telemetry)
    for a single request. It is bound to a single asm request context.
    It is contained into a ContextVar.
    """

    def __init__(self, span: Optional[Span] = None):
        self.root = not in_iast_context()
        if span is None:
            self.span: Span = core.get_item("call")
        else:
            self.span = span

        self.request_enabled: bool = True
        self.iast_reporter: Optional[IastSpanReporter] = None


def _get_iast_context() -> Optional[IASTEnvironment]:
    return core.get_item(_IAST_CONTEXT)


def in_iast_context() -> bool:
    return core.get_item(_IAST_CONTEXT) is not None


def start_iast_context():
    from ._taint_tracking import create_context as create_propagation_context

    if asm_config._iast_enabled:
        create_propagation_context()
        core.set_item(_IAST_CONTEXT, IASTEnvironment())


def end_iast_context(span: Optional[Span] = None):
    from ._taint_tracking import reset_context as reset_propagation_context

    env = _get_iast_context()
    if env is not None and env.span is span:
        finalize_iast_env(env)
    reset_propagation_context()


def finalize_iast_env(env: IASTEnvironment) -> None:
    core.discard_local_item(_IAST_CONTEXT)


def set_iast_reporter(iast_reporter: IastSpanReporter) -> None:
    env = _get_iast_context()
    if env:
        env.iast_reporter = iast_reporter
    else:
        log.debug("[IAST] Trying to set IAST reporter but no context is present")


def get_iast_reporter() -> Optional[IastSpanReporter]:
    env = _get_iast_context()
    if env:
        return env.iast_reporter
    return None


def set_iast_request_enabled(request_enabled) -> None:
    env = _get_iast_context()
    if env:
        env.request_enabled = request_enabled
    else:
        log.debug("[IAST] Trying to set IAST reporter but no context is present")


def is_iast_request_enabled():
    env = _get_iast_context()
    if env:
        return env.request_enabled
    return False
