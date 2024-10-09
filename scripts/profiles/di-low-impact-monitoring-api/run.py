import time
from types import FunctionType
import typing as t

from ddtrace.internal.wrapping.context import WrappingContext


def target(n=1_000_000_000):
    start = time.time_ns()
    c = 0
    while time.time_ns() - start < n:
        c += 1
    return c


def meta_target():
    start = time.time_ns()
    c = 0
    while time.time_ns() - start < 5_000_000_000:
        target(0)
        c += 1
    return c


WrappingContext(t.cast(FunctionType, target)).wrap()

print(meta_target())
