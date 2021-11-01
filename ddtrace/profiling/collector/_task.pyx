from ddtrace import compat
from ddtrace.internal import nogevent

from . import _threading


try:
    import gevent._tracer
    import gevent.thread
except ImportError:
    _gevent_tracer = None
else:
    class DDGreenletTracer(gevent._tracer.GreenletTracer):
        def _trace(self, event, args):
            # Do not trace gevent Hub: the Hub is a greenlet but we want to know the latest active greenlet *before*
            # the application yielded back to the Hub. There's no point showing the Hub most of the time to the users as
            # that does not give any information about user code.
            if not isinstance(args[1], gevent.hub.Hub):
                gevent._tracer.GreenletTracer._trace(self, event, args)

    # NOTE: bold assumption: this module is always imported by the MainThread.
    # A GreenletTracer is local to the thread instantiating it and we assume this is run by the MainThread.
    _gevent_tracer = DDGreenletTracer()


cpdef get_task(thread_id):
    """Return the task id and name for a thread."""
    # gevent greenlet support:
    # we only support tracing tasks in the greenlets are run in the MainThread.
    if thread_id == nogevent.main_thread_id and _gevent_tracer is not None:
        if _gevent_tracer.active_greenlet is None:
            # That means gevent never switch to another greenlet, we're still in the main one
            task_id = compat.main_thread.ident
            frame = None
        else:
            task_id = gevent.thread.get_ident(_gevent_tracer.active_greenlet)
            frame = _gevent_tracer.active_greenlet.gr_frame

        # Greenlets might be started as Thread in gevent
        task_name = _threading.get_thread_name(task_id)
    else:
        task_id = None
        task_name = None
        frame = None

    return task_id, task_name, frame
