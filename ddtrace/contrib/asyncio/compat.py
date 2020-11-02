import asyncio


def asyncio_current_task():
    if hasattr(asyncio, "current_task"):
        try:
            return asyncio.current_task()
        except RuntimeError:
            return None
    else:
        return asyncio.Task.current_task()
