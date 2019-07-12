try:
    from asyncio import current_task as asyncio_current_task
    from asyncio import create_task as asyncio_create_task
except ImportError:
    import asyncio
    from asyncio.base_events import BaseEventLoop
    asyncio_current_task = asyncio.Task.current_task
    asyncio_create_task = BaseEventLoop.create_task
