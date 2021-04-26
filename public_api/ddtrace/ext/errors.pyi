from typing import Any, Optional

ERROR_MSG: str
ERROR_TYPE: str
ERROR_STACK: str
MSG = ERROR_MSG
TYPE = ERROR_TYPE
STACK = ERROR_STACK

def get_traceback(tb: Optional[Any] = ..., error: Optional[Any] = ...): ...
