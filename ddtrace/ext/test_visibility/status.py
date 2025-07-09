import dataclasses
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Optional
from typing import Type

from ddtrace.ext.test import Status as _TestStatus
from ddtrace.ext.test_visibility._test_visibility_base import TestSourceFileInfoBase


class TestStatus(Enum):
    __test__ = False
    PASS = _TestStatus.PASS.value
    FAIL = _TestStatus.FAIL.value
    SKIP = _TestStatus.SKIP.value
    XFAIL = _TestStatus.XFAIL.value
    XPASS = _TestStatus.XPASS.value


@dataclasses.dataclass(frozen=True)
class TestExcInfo:
    __test__ = False
    exc_type: Type[BaseException]
    exc_value: BaseException
    exc_traceback: TracebackType


@dataclasses.dataclass(frozen=True)
class TestSourceFileInfo(TestSourceFileInfoBase):
    path: Path
    start_line: Optional[int] = None
    end_line: Optional[int] = None
