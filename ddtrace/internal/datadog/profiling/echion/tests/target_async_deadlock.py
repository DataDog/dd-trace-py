import asyncio
from asyncio import Task

from typing import Optional

# We'll assign these after creation so each coroutine can await the other.
t1: Optional[Task] = None
t2: Optional[Task] = None


async def task_a() -> None:
    assert t2 is not None
    print("task_a: awaiting task_b (deadlock starts)")
    try:
        await t2
    except asyncio.CancelledError:
        print("task_a: cancelled")


async def task_b() -> None:
    assert t1 is not None
    print("task_b: awaiting task_a (deadlock starts)")
    try:
        await t1
    except asyncio.CancelledError:
        print("task_b: cancelled")


async def main():
    global t1, t2

    # Create both tasks so they can reference each other.
    t1 = asyncio.create_task(task_a(), name="task_a")
    t2 = asyncio.create_task(task_b(), name="task_b")

    # Let the circular wait sit for 3 seconds.
    await asyncio.sleep(3)

    print("main: timeout hit after 3s, cancelling both tasks...")
    try:
        t1.cancel()
    except RecursionError:
        pass

    try:
        t2.cancel()
    except RecursionError:
        pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RecursionError:
        pass
