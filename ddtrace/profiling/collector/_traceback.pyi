import types
import typing

# (filename, line number, function name)
FrameType = typing.Tuple[str, int, str]

def traceback_to_frames(
    traceback: types.TracebackType, max_nframes: int
) -> typing.Tuple[typing.List[FrameType], int]: ...
def pyframe_to_frames(frame: types.FrameType, max_nframes: int) -> typing.Tuple[typing.List[FrameType], int]: ...
