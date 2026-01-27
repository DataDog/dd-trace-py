import types
import typing

from .. import event

def pyframe_to_frames(frame: types.FrameType, max_nframes: int) -> typing.List[event.DDFrame]: ...
