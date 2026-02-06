import types
import typing

def get_task(
    thread_id: int,
) -> tuple[typing.Optional[int], typing.Optional[str], typing.Optional[types.FrameType]]: ...
