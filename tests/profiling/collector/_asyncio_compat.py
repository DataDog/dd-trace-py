import asyncio
import sys


PY38_AND_LATER = sys.version_info[:2] >= (3, 8)
PY37_AND_LATER = sys.version_info[:2] >= (3, 7)
PY36_AND_LATER = sys.version_info[:2] >= (3, 6)

if PY37_AND_LATER:
    if PY38_AND_LATER:
        create_task = asyncio.create_task
    else:

        def create_task(coro, name=None):
            return asyncio.create_task(coro)

    run = asyncio.run

else:

    def run(main):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(main)

    def create_task(coro, name=None):
        return asyncio.ensure_future(coro)
