"""Shared helpers for the hand-written wrapping tests.

Each test takes a ``mech`` argument (a parametrized fixture defined in
``conftest.py``) and is run once per wrapping mechanism. ``mech`` exposes:

  * ``mech.wrap_function(fn) -> callable`` for module-level functions, closures,
    lambdas, generators, coroutines, async generators, and contextmanager
    underlyings.
  * ``mech.install_method(cls, attr)`` to wrap a method in place, preserving
    instance / classmethod / staticmethod binding (read from the descriptor).

A test that is expected to fail for a specific mechanism declares it at the test
site with ``@xfail_mechanism("wrapt", reason=...)`` (see mechanisms.py); for all
mechanisms, use a plain ``@pytest.mark.xfail``.
"""

import asyncio
import functools


def wraps_deco(fn):
    """A transparent ``functools.wraps`` pass-through decorator.

    Used to test that a wrapping mechanism composes correctly when stacked above
    or below an ordinary user decorator (decorator order).
    """

    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return _wrapper


def run(coro):
    """Run a coroutine on a private event loop, swallowing teardown noise.

    A wrapping bug can leave a corrupted async generator whose athrow/aclose
    errors during GC; that divergence is already asserted by the test, so we keep
    loop teardown quiet.
    """
    loop = asyncio.new_event_loop()
    loop.set_exception_handler(lambda _loop, _ctx: None)
    try:
        return loop.run_until_complete(coro)
    finally:
        try:
            for task in asyncio.all_tasks(loop):
                task.cancel()
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()


async def aiterate(agen):
    """Collect an async generator to a list."""
    return [item async for item in agen]
