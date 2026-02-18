from types import FrameType

class Frame:
    file: str
    name: str
    line: int
    line_end: int = 0
    column: int = 0
    column_end: int = 0

def unwind_current_thread(max_depth: int = 0) -> list[Frame]: ...
def unwind_from_frame(frame: FrameType, max_depth: int = 0) -> list[Frame]: ...
def set_frame_cache_size(size: int) -> None: ...
def get_frame_cache_size() -> int: ...
