import types

from .. import event

def pyframe_to_frames(frame: types.FrameType, max_nframes: int) -> list[event.DDFrame]: ...
