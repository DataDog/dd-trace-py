import typing as t

class Frame:
    file: str
    name: str
    line: int
    line_end: int = 0
    column: int = 0
    column_end: int = 0

def unwind_current_thread(n: int = 0) -> t.List[Frame]: ...
