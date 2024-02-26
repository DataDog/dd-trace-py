from types import CodeType
from types import FrameType

from ddtrace.internal.logger import get_logger
from ddtrace.profiling.event import DDFrame


log = get_logger(__name__)


cpdef _extract_class_name(frame):
    # type: (...) -> str
    """Extract class name from a frame, if possible.

    :param frame: The frame object.
    """
    code = frame.f_code
    if code.co_argcount > 0:
        # Retrieve the name of the first argument, if the code object has any
        argname = code.co_varnames[0]
        try:
            value = frame.f_locals[argname]
        except Exception:
            log.debug("Unable to extract class name from frame %r", frame, exc_info=True)
            return ""
        try:
            if argname == "self":
                return object.__getattribute__(type(value), "__name__")  # use type() and object.__getattribute__ to avoid side-effects
            if argname == "cls":
                return object.__getattribute__(value, "__name__")
        except AttributeError:
            return ""
    return ""


cpdef traceback_to_frames(traceback, max_nframes):
    """Serialize a Python traceback object into a list of tuple of (filename, lineno, function_name).

    :param traceback: The traceback object to serialize.
    :param max_nframes: The maximum number of frames to return.
    :return: The serialized frames and the number of frames present in the original traceback.
    """
    tb = traceback
    frames = []
    nframes = 0
    while tb is not None:
        if nframes < max_nframes:
            frame = tb.tb_frame
            code = frame.f_code
            lineno = 0 if frame.f_lineno is None else frame.f_lineno
            frames.insert(0, DDFrame(code.co_filename, lineno, code.co_name, _extract_class_name(frame)))
        nframes += 1
        tb = tb.tb_next
    return frames, nframes


cpdef pyframe_to_frames(frame, max_nframes):
    """Convert a Python frame to a list of frames.

    :param frame: The frame object to serialize.
    :param max_nframes: The maximum number of frames to return.
    :return: The serialized frames and the number of frames present in the original traceback."""
    # DEV: There are reports that Python 3.11 returns non-frame objects when
    # retrieving frame objects and doing stack unwinding. If we detect a
    # non-frame object we log a warning and return an empty stack, to avoid
    # reporting potentially incomplete and/or inaccurate data. This until we can
    # come to the bottom of the issue.
    if not isinstance(frame, FrameType):
        log.warning(
            "Got object of type '%s' instead of a frame object for the top frame of a thread", type(frame).__name__
        )
        return [], 0

    frames = []
    nframes = 0

    while frame is not None:
        IF PY_VERSION_HEX >= 0x030b0000:
            if not isinstance(frame, FrameType):
                log.warning(
                    "Got object of type '%s' instead of a frame object during stack unwinding", type(frame).__name__
                )
                return [], 0

        if nframes < max_nframes:
            code = frame.f_code
            IF PY_VERSION_HEX >= 0x030b0000:
                if not isinstance(code, CodeType):
                    log.warning(
                        "Got object of type '%s' instead of a code object during stack unwinding", type(code).__name__
                    )
                    return [], 0

            lineno = 0 if frame.f_lineno is None else frame.f_lineno
            frames.append(DDFrame(code.co_filename, lineno, code.co_name, _extract_class_name(frame)))
        nframes += 1
        frame = frame.f_back
    return frames, nframes
