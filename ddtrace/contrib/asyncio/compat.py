import asyncio


if hasattr(asyncio, "current_task"):

    def asyncio_current_task():
        try:
            return asyncio.current_task()
        except RuntimeError:
            return None


else:

    def asyncio_current_task():
        return asyncio.Task.current_task()
