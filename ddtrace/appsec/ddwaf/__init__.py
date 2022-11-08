import logging
from typing import Any
from typing import Union

from ddtrace.internal.compat import PY3


LOGGER = logging.getLogger(__name__)  # type: logging.Logger

# Python 2/3 unicode str compatibility
if PY3:
    unicode = str

#
# Interface as Cython
#

DEFAULT_DDWAF_TIMEOUT_MS = 20

# Mockup of the DDWaf class doing nothing
class DDWaf(object):  # type: ignore
    required_data = []  # type: list[unicode]
    info = {}  # type: dict[unicode, Any]

    def __init__(self, rules, obfuscation_parameter_key_regexp, obfuscation_parameter_value_regexp):
        # type: (DDWaf, Union[None, int, unicode, list[Any], dict[unicode, Any]], unicode, unicode) -> None
        pass

    def run(
        self,  # type: DDWaf
        data,  # type: Union[None, int, unicode, list[Any], dict[unicode, Any]]
        timeout_ms=DEFAULT_DDWAF_TIMEOUT_MS,  # type:int
    ):
        # type: (...) -> tuple[unicode, float, float]
        LOGGER.warning("DDWaf features disabled. dry run")
        return ("", 0.0, 0.0)


def version():
    # type: () -> unicode
    LOGGER.warning("DDWaf features disabled. null version")
    return "0.0.0"
