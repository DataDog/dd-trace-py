"""Shared helpers for the hand-written wrapping tests.

Each test defines a real, readable callable and asserts the *explicit* expected
behaviour after wrapping. Tests are parametrized over the four wrapping
mechanisms via :data:`mechanisms` (or :func:`mechanisms_param` when a specific
mechanism is a known-failure and should be xfailed).

The ``mech`` object passed to each test exposes:

  * ``mech.wrap_function(fn) -> callable`` for module-level functions, closures,
    lambdas, generators, coroutines, async generators, and contextmanager
    underlyings.
  * ``mech.install_method(cls, attr, binding)`` to wrap a method in place,
    preserving instance / classmethod / staticmethod binding.
"""

import asyncio
import functools
import sys

import pytest

from tests.wrapping.mechanisms import ALL_MECHANISMS


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


# ---------------------------------------------------------------------------
# Known wrapping defects (the suite's xfail registry)
# ---------------------------------------------------------------------------
# The documented, currently-unfixed defects this suite xfails live here, in one
# place, so they are greppable together and each predicate is written once. All
# are passed to ``mechanisms_param(xfail=...)``, whose markers are strict=True --
# when a product fix lands the case XPASSes and fails CI, which is the signal to
# delete the corresponding entry below and re-enable the hard assertion. Reason
# strings stay terse here; the README carries the full table.
#
#   1. G22                  internal_wrap  all    generator return value dropped
#   2. PR #18741            internal_wrap  3.11+  async-gen await machinery corrupted
#   3. WCTX async-gen       wrapping_ctx   3.10   asend/athrow/aclose -> internal TypeError
#   4. WCTX unstarted-throw wrapping_ctx   3.11+  throw() into unstarted gen -> AttributeError
#   5. WCTX t-string        wrapping_ctx   3.14   bytecode lib can't re-encode t-string opcodes
#
# #2 is detected by PROBING the actual behaviour rather than keying on a version:
# the PR #18741 fix is already on main, so a version-only predicate would turn the
# strict=True xfails into XPASS failures the moment this branch is rebased/merged.
# The probe keeps the xfail correct whether or not the tree contains the fix.
# (#2 is immune on 3.9/3.10 which use YIELD_FROM; #3's plain __anext__ iteration is
# fine, only the bidirectional protocol breaks -- hence the ITER vs SEND split.)


def _internal_wrap_corrupts_awaiting_async_gen():
    """Return True iff internal wrap() still corrupts an async generator that
    awaits around its yield (the PR #18741 defect).
    """
    if sys.version_info < (3, 11):
        return False  # 3.9/3.10 drive async-gens via YIELD_FROM and are immune

    async def _probe():
        await asyncio.sleep(0)
        yield 1

    ALL_MECHANISMS["internal_wrap"].wrap_function(_probe)  # mutates __code__ in place

    async def _drive():
        async for _ in _probe():
            pass

    try:
        run(_drive())
    except BaseException:  # noqa: BLE001 - any failure means the defect is present
        return True
    return False


_PR18741_PRESENT = _internal_wrap_corrupts_awaiting_async_gen()


def _agen_xfails(send_protocol: bool):
    xf = {}
    if _PR18741_PRESENT:  # #2, probed (not version-keyed) -- see note above
        xf["internal_wrap"] = "PR#18741: async-gen await machinery corrupted (asyncs.py @loop vs @presend0)"
    if send_protocol and sys.version_info[:2] == (3, 10):  # #3
        xf["wrapping_context"] = (
            "WrappingContext async-gen asend/athrow/aclose broken on 3.10 (TypeError: NoneType not callable)"
        )
    return xf


# #2 + #3: async-gen tests driven only via __anext__ (iterate, multi-suspend).
AGEN_ITER_XFAIL = _agen_xfails(send_protocol=False)
# #2 + #3: async-gen tests using the bidirectional protocol (asend/athrow/aclose).
AGEN_SEND_XFAIL = _agen_xfails(send_protocol=True)

# #1: internal wrap() drops a generator's return value (generators.py @stopiter
# returns None) on every version.
G22_XFAIL = {
    "internal_wrap": "G22: internal wrap() drops generator return value (generators.py @stopiter returns None)"
}

# #4: WrappingContext.throw() into an unstarted generator crashes on 3.11+ with an
# internal AttributeError during teardown (correct on 3.9/3.10).
WCTX_UNSTARTED_XFAIL = (
    {"wrapping_context": "WrappingContext.throw() on an unstarted generator crashes on 3.11+ (internal AttributeError)"}
    if sys.version_info >= (3, 11)
    else {}
)

# #5: a t-string in the body crashes WrappingContext.wrap() -- the bytecode lib
# (<=0.18.1) can't re-encode PEP 750 opcodes. Consumed only by the 3.14-gated file.
TSTRING_XFAIL = {
    "wrapping_context": "bytecode lib can't re-encode PEP 750 t-string opcodes; WrappingContext.wrap() crashes (3.14)",
}


#: Parametrize a test over all four mechanisms (no expected failures).
mechanisms = pytest.mark.parametrize("mech", list(ALL_MECHANISMS.values()), ids=list(ALL_MECHANISMS))


def mechanisms_param(xfail=None):
    """Parametrize over the four mechanisms, xfailing the named ones.

    ``xfail`` maps a mechanism name to an xfail reason. Build it conditionally
    (e.g. only on certain Python versions, or by probing the defect) at call sites
    so the marker is exact.

    Markers are ``strict=True``: a documented defect that starts *passing* (e.g.
    a product fix lands) turns the XPASS into a hard failure, forcing the stale
    marker to be removed and the shape to be hard-asserted again. This is what
    keeps the suite's promise that any new regression is a failure.
    """
    xfail = xfail or {}
    params = []
    for name, mech in ALL_MECHANISMS.items():
        marks = [pytest.mark.xfail(reason=xfail[name], strict=True)] if name in xfail else []
        params.append(pytest.param(mech, id=name, marks=marks))
    return pytest.mark.parametrize("mech", params)
