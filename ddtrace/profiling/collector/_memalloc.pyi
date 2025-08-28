import typing
from collections import namedtuple

# (filename, line number, function name, class name)
DDFrame = namedtuple("DDFrame", ["file_name", "lineno", "function_name", "class_name"])
StackTraceType = typing.List[DDFrame]

# (stack, thread_id)
TracebackType = typing.Tuple[StackTraceType, int]

def start(max_nframe: int, heap_sample_interval: int) -> None: ...
def stop() -> None: ...
def heap() -> typing.List[typing.Tuple[TracebackType, int, int, int]]: ...
