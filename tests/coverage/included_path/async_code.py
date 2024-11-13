import asyncio


async def some_async_function():
    return 42


def call_async_function():
    asyncio.run(some_async_function())
