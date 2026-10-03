# NOTE(taegyunkim): Consider using ThreadInfoMap in echion to get thread name,
# thread native_id instead of maintaining a separate mechanism here. Using
# echion's map would increase contention but we'd have one less module to
# maintain.
import threading
from typing import Optional

def get_thread_by_id(thread_id: int) -> Optional[threading.Thread]: ...
def get_thread_name(thread_id: int) -> Optional[str]: ...
def get_thread_native_id(thread_id: int) -> int: ...
