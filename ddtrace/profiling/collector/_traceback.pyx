from types import CodeType
from types import FrameType

from ddtrace.internal.logger import get_logger
from ddtrace.profiling.event import DDFrame


log = get_logger(__name__)


cpdef pyframe_to_frames(frame, max_nframes):
    """Convert a Python frame to a list of frames.

    :param frame: The frame object to serialize.
    :param max_nframes: The maximum number of frames to return.
    :return: The serialized frames."""
    # DEV: There are reports that Python 3.11 returns non-frame objects when
    # retrieving frame objects and doing stack unwinding. If we detect a
    # non-frame object we log a warning and return an empty stack, to avoid
    # reporting potentially incomplete and/or inaccurate data. This until we can
    # come to the bottom of the issue.
    if not isinstance(frame, FrameType):
        log.warning(
            "Got object of type '%s' instead of a frame object for the top frame of a thread", type(frame).__name__
        )
        return []

    frames = []
    nframes = 0

    while frame is not None:
        IF PY_VERSION_HEX >= 0x030b0000:
            if not isinstance(frame, FrameType):
                log.warning(
                    "Got object of type '%s' instead of a frame object during stack unwinding", type(frame).__name__
                )
                return []

        if nframes < max_nframes:
            code = frame.f_code
            IF PY_VERSION_HEX >= 0x030b0000:
                if not isinstance(code, CodeType):
                    log.warning(
                        "Got object of type '%s' instead of a code object during stack unwinding", type(code).__name__
                    )
                    return []

            lineno = 0 if frame.f_lineno is None else frame.f_lineno
            IF PY_VERSION_HEX >= 0x030b0000:
                frames.append(DDFrame(code.co_filename, lineno, code.co_qualname))
            ELSE:
                frames.append(DDFrame(code.co_filename, lineno, code.co_name))
        nframes += 1
        frame = frame.f_back
    return frames
