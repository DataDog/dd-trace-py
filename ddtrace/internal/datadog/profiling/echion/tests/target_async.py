import asyncio
import time


async def foobar():
    time.sleep(0.5)


async def baz():
    await foobar()
    await asyncio.sleep(0.5)


async def bar():
    await baz()
    await asyncio.create_task(baz(), name="Task-baz")


async def foo():
    await asyncio.create_task(bar(), name="Task-bar")


async def main():
    await foo()


if __name__ == "__main__":
    asyncio.run(main())
