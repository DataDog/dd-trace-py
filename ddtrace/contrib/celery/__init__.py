"""
The Celery integration will trace all tasks that are executed in the
background. Functions and class based tasks are traced only if the Celery API
is used, so calling the function directly or via the ``run()`` method will not
generate traces. However, calling ``apply()``, ``apply_async()`` and ``delay()``
will produce tracing data. To trace your Celery application, call the patch method::

    import celery
    from ddtrace import patch

    patch(celery=True)
    app = celery.Celery()

    @app.task
    def my_task():
        pass

    class MyTask(app.Task):
        def run(self):
            pass


To change Celery service name, you can use the ``Config`` API as follows::

    from ddtrace import config

    # change service names for producers and workers
    config.celery['producer_service_name'] = 'task-queue'
    config.celery['worker_service_name'] = 'worker-notify'

By default, reported service names are:
    * ``celery-producer`` when tasks are enqueued for processing
    * ``celery-worker`` when tasks are processed by a Celery process


Distributed Tracing across celery tasks

By default, ddtrace does not propagate trace_id when calling tasks, when you
call ``apply_async()`` from your main application or from another celery task,
the child task will be assigned a new trace.

If you want to follow a trace across when you call a celery tasks, you will
need to pass trace_id along to the child task. ddtrace currently don't have
trace propagator for celery tasks, but you can use Celery-OpenTracing and
ddtrace's opentracing support. To do this:

1. Install ddtrace with opentracing support and Celery-OpenTracing:

    pip install ddtrace[opentracing] Celery-OpenTracing

2. Replace your Celery app with the version that comes with Celery-OpenTracing:

    from celery_opentracing import CeleryTracing
    from ddtrace.opentracer import set_global_tracer, Tracer

    ddtracer = Tracer()
    set_global_tracer(ddtracer)

    app = CeleryTracing(app, tracer=ddtracer)

"""
from ...utils.importlib import require_modules


required_modules = ['celery']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .app import patch_app, unpatch_app
        from .patch import patch, unpatch
        from .task import patch_task, unpatch_task

        __all__ = [
            'patch',
            'patch_app',
            'patch_task',
            'unpatch',
            'unpatch_app',
            'unpatch_task',
        ]
