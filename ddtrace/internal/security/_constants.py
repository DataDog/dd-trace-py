from typing import Literal

from ddtrace.internal.constants import Constant_Class


class STACK_TRACE(metaclass=Constant_Class):
    RASP = "exploit"
    IAST = "vulnerability"
    TAG: Literal["_dd.stack"] = "_dd.stack"
