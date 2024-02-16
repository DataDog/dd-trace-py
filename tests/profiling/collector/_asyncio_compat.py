import asyncio
import sys


PY38_AND_LATER = sys.version_info[:2] >= (3, 8)

if PY38_AND_LATER:
    create_task = asyncio.create_task
else:

    def create_task(coro, name=None):
        return asyncio.create_task(coro)


run = asyncio.run
