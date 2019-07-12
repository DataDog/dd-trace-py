try:
    # >= py37
    from asyncio import current_task as asyncio_current_task
except ImportError:
    # < py37
    import asyncio
    asyncio_current_task = asyncio.Task.current_task
