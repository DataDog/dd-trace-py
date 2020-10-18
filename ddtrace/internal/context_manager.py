import abc
from ddtrace.vendor import six

from ddtrace.compat import contextvars
from .logger import get_logger
from ..context import Context

log = get_logger(__name__)

_DD_CONTEXTVAR = contextvars.ContextVar("datadog_contextvar", default=None)


class BaseContextManager(six.with_metaclass(abc.ABCMeta)):
    def __init__(self, reset=True):
        if reset:
            self.reset()

    @abc.abstractmethod
    def _has_active_context(self):
        pass

    @abc.abstractmethod
    def set(self, ctx):
        pass

    @abc.abstractmethod
    def get(self):
        pass

    def reset(self):
        pass


class ContextVarContextManager(BaseContextManager):
    def _has_active_context(self):
        ctx = _DD_CONTEXTVAR.get()
        return ctx is not None

    def set(self, ctx):
        _DD_CONTEXTVAR.set(ctx)

    def get(self):
        ctx = _DD_CONTEXTVAR.get()
        if not ctx:
            ctx = Context()
            self.set(ctx)

        return ctx

    def reset(self):
        _DD_CONTEXTVAR.set(None)


DefaultContextManager = ContextVarContextManager
