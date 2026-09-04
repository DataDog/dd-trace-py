"""Tell apart instrumentation frames an exception passed through from ones that raised it.

Monkey-patching puts a ddtrace frame in the traceback of ordinary application errors, which makes
crash intake attribute the customer's bug to us. The reporting boundaries use this to drop those.
"""

import dis
import traceback
from types import CodeType
from types import TracebackType
from typing import Any
from typing import Optional


# Code objects of wrappers that exist to forward a call to the callable they wrap. Registered
# rather than inferred, so a frame is only dropped where we know what the function is there for.
_passthrough_codes: set[CodeType] = set()


def mark_passthrough(wrapper: Any) -> None:
    """Record a wrapper whose frame must not be blamed for exceptions raised beneath it."""
    code = getattr(wrapper, "__code__", None)
    if isinstance(code, CodeType):
        _passthrough_codes.add(code)


def _left_through_a_call(tb: TracebackType) -> bool:
    """Did the exception leave this frame through a call, rather than originate inside it?

    A Python callee would own the deepest frame itself, so a frame that is both deepest and
    stopped on a call instruction was forwarding to a C callable such as builtins.open.
    """
    code = tb.tb_frame.f_code
    lasti = tb.tb_lasti
    if lasti < 0 or lasti >= len(code.co_code):
        return False
    return dis.opname[code.co_code[lasti]].startswith("CALL")


def extract_reportable_frames(exc_traceback: Optional[TracebackType]) -> traceback.StackSummary:
    """Extract a traceback, minus the instrumentation frames the exception merely passed through.

    A registered wrapper frame is kept when the exception originated in the wrapper itself, so
    genuine ddtrace faults stay attributed to us.
    """
    summaries = traceback.extract_tb(exc_traceback)
    if not _passthrough_codes or exc_traceback is None:
        return summaries

    tbs = []
    tb: Optional[TracebackType] = exc_traceback
    while tb is not None:
        tbs.append(tb)
        tb = tb.tb_next

    if len(tbs) != len(summaries):
        # sys.tracebacklimit truncated extract_tb but not the walk above, so the two no longer
        # line up and a frame cannot be matched to its code object. Report the traceback as is.
        return summaries

    last = len(tbs) - 1
    kept = traceback.StackSummary.from_list(
        [
            summary
            for index, (summary, frame_tb) in enumerate(zip(summaries, tbs))
            if not (
                frame_tb.tb_frame.f_code in _passthrough_codes and (index != last or _left_through_a_call(frame_tb))
            )
        ]
    )

    # Everything was ours and forwarding: keep the traceback rather than report an empty one.
    return kept or summaries
