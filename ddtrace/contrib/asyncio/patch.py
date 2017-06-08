# project
import ddtrace
from ddtrace.util import unwrap
from ddtrace.provider import DefaultContextProvider

# 3p
import wrapt
import asyncio

from .helpers import _wrapped_create_task
from . import context_provider

_orig_create_task = asyncio.BaseEventLoop.create_task


def patch(tracer=ddtrace.tracer):
    """
    Patches `BaseEventLoop.create_task` to enable spawned tasks to parent to
    the base task context.  Will also enable the asyncio task context.
    """
    # TODO: figure what to do with helpers.ensure_future and
    # helpers.run_in_executor (doesn't work for ProcessPoolExecutor)
    if getattr(asyncio, '_datadog_patch', False):
        return
    setattr(asyncio, '_datadog_patch', True)

    tracer.configure(context_provider=context_provider)
    wrapt.wrap_function_wrapper('asyncio', 'BaseEventLoop.create_task', _wrapped_create_task)


def unpatch(tracer=ddtrace.tracer):
    """
    Remove tracing from patched modules.
    """
    if getattr(asyncio, '_datadog_patch', False):
        setattr(asyncio, '_datadog_patch', False)

        tracer.configure(context_provider=DefaultContextProvider())
        unwrap(asyncio.BaseEventLoop, 'create_task')
