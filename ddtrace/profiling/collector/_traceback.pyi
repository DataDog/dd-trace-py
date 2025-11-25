import types
import typing

from .. import event

def pyframe_to_frames(frame: types.FrameType, max_nframes: int) -> typing.Tuple[typing.List[event.DDFrame], int]: ...
