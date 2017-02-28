from tornado.stack_context import StackContextInconsistentError, _state

from ...context import Context


class TracerStackContext(object):
    """
    A context manager that manages ``Context`` instances in a thread-local state.
    It must be used everytime a Tornado's handler or coroutine is used within a
    tracing Context. It is meant to work like a traditional ``StackContext``,
    preserving the state across asynchronous calls.

    Everytime a new manager is initialized, a new ``Context()`` is created for
    this execution flow. Context created in a ``TracerStackContext`` is not shared
    between different threads.
    """
    def __init__(self):
        self.active = True
        self.context = Context()

    def enter(self):
        """
        Used to preserve the ``StackContext`` interface.
        """
        pass

    def exit(self, type, value, traceback):
        """
        Used to preserve the ``StackContext`` interface.
        """
        pass

    def __enter__(self):
        self.old_contexts = _state.contexts
        self.new_contexts = (self.old_contexts[0] + (self,), self)
        _state.contexts = self.new_contexts
        return self

    def __exit__(self, type, value, traceback):
        final_contexts = _state.contexts
        _state.contexts = self.old_contexts

        if final_contexts is not self.new_contexts:
            raise StackContextInconsistentError(
                'stack_context inconsistency (may be caused by yield '
                'within a "with TracerStackContext" block)')

        # break the reference to allow faster GC on CPython
        self.new_contexts = None

    def deactivate(self):
        self.active = False

    @classmethod
    def current_context(cls):
        """
        Return the ``Context`` from the current execution flow. This method can be
        used inside a Tornado coroutine to retrieve and use the current tracing context.
        """
        for ctx in reversed(_state.contexts[0]):
            if isinstance(ctx, cls) and ctx.active:
                return ctx.context


def run_with_trace_context(context, func, *args, **kwargs):
    """
    Helper function that runs a function or a coroutine in the given context.
    """
    with context:
        return func(*args, **kwargs)
