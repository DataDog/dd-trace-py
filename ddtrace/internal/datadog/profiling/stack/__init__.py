# See ../ddup/__init__.py for some discussion on the is_available attribute.
# This component is also loaded in ddtrace/settings/profiling.py
is_available = False
failure_msg = ""


try:
    from . import _stack
    from ._stack import *  # noqa: F403, F401  # type: ignore[assignment]

    is_available = True

    def link_origin_task(task_id: int, task_name: str) -> None:
        """
        Record, for the current thread, the asyncio task that submitted the work now running on it.
        """
        _stack.link_origin_task(task_id, task_name)

    def unlink_origin_task() -> None:
        """
        Clear the originating asyncio task for the current thread.
        """
        _stack.unlink_origin_task()

except Exception as e:
    failure_msg = str(e)
