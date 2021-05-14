import typing

# (filename, line number, function name)
FrameType = typing.Tuple[str, int, str]

StackType = typing.Tuple[FrameType, ...]

# (stack, nframe, thread_id)
TracebackType = typing.Tuple[StackType, int, int]

def start(max_nframe: int, max_events: int, heap_sample_size: int) -> None: ...
def stop() -> None: ...
def heap() -> typing.List[typing.Tuple[TracebackType, int]]: ...
def iter_events() -> typing.Iterator[typing.Tuple[TracebackType, int]]: ...
