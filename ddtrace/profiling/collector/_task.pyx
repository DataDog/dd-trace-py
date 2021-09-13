from ddtrace import compat
from ddtrace.internal import nogevent

from . import _threading


try:
    import gevent._tracer
except ImportError:
    _gevent_tracer = None
else:
    # NOTE: bold assumption: this module is always imported by the MainThread.
    # A GreenletTracer is local to the thread instantiating it and we assume this is run by the MainThread.
    _gevent_tracer = gevent._tracer.GreenletTracer()


cpdef get_task(thread_id):
    """Return the task id and name for a thread."""
    # gevent greenlet support:
    # we only support tracing tasks in the greenlets are run in the MainThread.
    if thread_id == nogevent.main_thread_id and _gevent_tracer is not None:
        if _gevent_tracer.active_greenlet is None:
            # That means gevent never switch to another greenlet, we're still in the main one
            task_id = compat.main_thread.ident
        else:
            task_id = gevent.thread.get_ident(_gevent_tracer.active_greenlet)

        # Greenlets might be started as Thread in gevent
        task_name = _threading.get_thread_name(task_id)
    else:
        task_id = None
        task_name = None

    return task_id, task_name
