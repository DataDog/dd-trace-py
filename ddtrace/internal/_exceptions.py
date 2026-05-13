from typing import Optional
from typing import TypeVar


class DDBlockException(BaseException):
    """
    Base class for any in-tree decision to abort the current operation
    (web request blocking, AI Guard policy abort, future product blocks).
    Inherits from BaseException so a generic ``except Exception:`` handler in
    user code does not accidentally swallow a blocking decision.
    """


class BlockingException(DDBlockException):
    """
    Exception raised when a request is blocked by ASM
    It derives from BaseException (via DDBlockException) to avoid being caught by the general Exception handler
    """


E = TypeVar("E", bound=BaseException)


def find_exception(
    exc: BaseException,
    exception_type: type[E],
) -> Optional[E]:
    """Traverse an exception and its children to find the first occurrence of a specific exception type."""
    if isinstance(exc, exception_type):
        return exc
    # The check matches both native Python3.11+ and `exceptiongroup` compatibility package versions of ExceptionGroup
    if exc.__class__.__name__ in ("BaseExceptionGroup", "ExceptionGroup") and hasattr(exc, "exceptions"):
        for sub_exc in exc.exceptions:
            found = find_exception(sub_exc, exception_type)
            if found:
                return found
    return None
