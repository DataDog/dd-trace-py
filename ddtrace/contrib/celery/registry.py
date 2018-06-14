from .task import patch_task


def _wrap_register(func, instance, args, kwargs):
    """Wraps the `TaskRegistry.register` function so that everytime
    a `Task` is registered it is properly instrumented. This wrapper
    is required because in old-style tasks (Celery 1.0+) we cannot
    instrument the base class, otherwise a `Strategy` `KeyError`
    exception is raised.
    """
    # the original signature requires one positional argument so the
    # first and only parameter is the `Task` that must be instrumented
    task = args[0]
    patch_task(task)
    func(*args, **kwargs)
