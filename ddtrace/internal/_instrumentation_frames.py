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


# Depth cap when peeling partial/__wrapped__ layers off a registered wrapper.
_MAX_WRAPPER_DEPTH = 10

# Code objects of wrappers that exist to forward a call to the callable they wrap. Registered
# rather than inferred, so a frame is only dropped where we know what the function is there for.
_passthrough_codes: set[CodeType] = set()


def mark_passthrough(*wrappers: Any) -> None:
    """Record wrappers whose frames must not be blamed for exceptions raised beneath them.

    Each is peeled through partial, bound-method and __wrapped__ layers: a functools.partial has
    no __code__ of its own, so registering one would otherwise be a silent no-op.
    """
    for wrapper in wrappers:
        for _ in range(_MAX_WRAPPER_DEPTH):
            if wrapper is None:
                break
            code = getattr(wrapper, "__code__", None)
            if isinstance(code, CodeType):
                _passthrough_codes.add(code)
            inner = getattr(wrapper, "func", None) or getattr(wrapper, "__func__", None)
            if inner is None:
                inner = getattr(wrapper, "__wrapped__", None)
            if inner is wrapper:
                break
            wrapper = inner


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

    A registered wrapper frame that is the deepest frame is kept when the exception originated in
    the wrapper rather than in something it called.

    Known limitation: a registered wrapper that is *not* the deepest frame is always dropped, even
    if its own code raised, because a traceback does not say which callee was the wrapped one. The
    bias is deliberate - this exists to stop blaming Datadog for application errors - and the
    registered wrappers either swallow their own exceptions or fail through ddtrace frames that
    stay in the report. See test_a_registered_wrapper_that_fails_through_a_python_callee.
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
