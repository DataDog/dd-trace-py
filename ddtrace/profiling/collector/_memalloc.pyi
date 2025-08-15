import typing

from .. import event

# (filename, line number, function name)
FrameType = event.DDFrame
StackType = event.StackTraceType

# (stack, thread_id)
TracebackType = typing.Tuple[StackType, int]

def start(max_nframe: int, heap_sample_interval: int) -> None: ...
def stop() -> None: ...
def heap() -> typing.List[typing.Tuple[TracebackType, int, int, int]]: ...
