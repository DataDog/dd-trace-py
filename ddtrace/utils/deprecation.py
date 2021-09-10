from functools import wraps
import os
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional
from typing import Tuple
import warnings

from ddtrace.vendor import debtcollector


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


def get_service_legacy(default=None):
    # type: (Optional[str]) -> Optional[str]
    """Helper to get the old {DD,DATADOG}_SERVICE_NAME environment variables
    and output a deprecation warning if they are defined.

    Note that this helper should only be used for migrating integrations which
    use the {DD,DATADOG}_SERVICE_NAME variables to the new DD_SERVICE variable.

    If the environment variables are not in use, no deprecation warning is
    produced and `default` is returned.
    """
    for old_env_key in ["DD_SERVICE_NAME", "DATADOG_SERVICE_NAME"]:
        if old_env_key in os.environ:
            debtcollector.deprecate(
                (
                    "'{}' is deprecated and will be removed in a future version. Please use DD_SERVICE instead. "
                    "Refer to our release notes on Github: https://github.com/DataDog/dd-trace-py/releases/tag/v0.36.0 "
                    "for the improvements being made for service names."
                ).format(old_env_key)
            )
            return os.getenv(old_env_key)

    return default
