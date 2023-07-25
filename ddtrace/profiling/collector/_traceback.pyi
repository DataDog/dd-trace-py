import types
import typing

from .. import event

def _extract_class_name(frame: types.FrameType) -> str: ...
def traceback_to_frames(
    traceback: types.TracebackType, max_nframes: int
) -> typing.Tuple[typing.List[event.FrameType], int]: ...
def pyframe_to_frames(frame: types.FrameType, max_nframes: int) -> typing.Tuple[typing.List[event.FrameType], int]: ...
