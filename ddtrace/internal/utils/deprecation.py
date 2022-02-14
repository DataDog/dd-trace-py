from functools import wraps
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional
from typing import Tuple
import warnings


class RemovedInDDTrace10Warning(DeprecationWarning):
    pass


def format_message(name, message, version=None):
    # type: (str, str, Optional[str]) -> str
    """Message formatter to create `DeprecationWarning` messages
    such as:

        'fn' is deprecated and will be remove in future versions (1.0).
    """
    return "'{}' is deprecated and will be remove in future versions{}. {}".format(
        name,
        " ({})".format(version) if version is not None else "",
        message,
    )


def warn(message, stacklevel=2):
    # type: (str, int) -> None
    """Helper function used as a ``DeprecationWarning``."""
    warnings.warn(message, RemovedInDDTrace10Warning, stacklevel=stacklevel)


def deprecation(name="", message="", version=None):
    # type: (str, str, Optional[str]) -> None
    """Function to report a ``DeprecationWarning``. Bear in mind that `DeprecationWarning`
    are ignored by default so they're not available in user logs. To show them,
    the application must be launched with a special flag:

        $ python -Wall script.py

    This approach is used by most of the frameworks, including Django
    (ref: https://docs.djangoproject.com/en/2.0/howto/upgrade-version/#resolving-deprecation-warnings)
    """
    msg = format_message(name, message, version)
    warn(msg, stacklevel=4)


def deprecated(message="", version=None):
    # type: (str, Optional[str]) -> Callable[[Callable[..., Any]], Callable[..., Any]]
    """Decorator function to report a ``DeprecationWarning``. Bear
    in mind that `DeprecationWarning` are ignored by default so they're
    not available in user logs. To show them, the application must be launched
    with a special flag:

        $ python -Wall script.py

    This approach is used by most of the frameworks, including Django
    (ref: https://docs.djangoproject.com/en/2.0/howto/upgrade-version/#resolving-deprecation-warnings)
    """

    def decorator(func):
        # type: (Callable[..., Any]) -> Callable[..., Any]
        @wraps(func)
        def wrapper(*args, **kwargs):
            # type: (Tuple[Any], Dict[str, Any]) -> Any
            msg = format_message(func.__name__, message, version)
            warn(msg, stacklevel=3)
            return func(*args, **kwargs)

        return wrapper

    return decorator
