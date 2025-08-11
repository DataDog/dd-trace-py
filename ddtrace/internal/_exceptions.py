from typing import Optional
from typing import Type
from typing import TypeVar


class BlockingException(BaseException):
    """
    Exception raised when a request is blocked by ASM
    It derives from BaseException to avoid being caught by the general Exception handler
    """


E = TypeVar("E", bound=BaseException)


def find_exception(
    exc: BaseException,
    exception_type: Type[E],
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
