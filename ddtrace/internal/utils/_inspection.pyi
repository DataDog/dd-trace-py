from types import FrameType
import typing as t

class Frame:
    file: str
    name: str
    line: int
    line_end: int = 0
    column: int = 0
    column_end: int = 0

def unwind_current_thread(frame: t.Optional[FrameType] = None) -> list[Frame]: ...
def set_frame_cache_size(size: int) -> None: ...
def get_frame_cache_size() -> int: ...
