"""
Supported versions:

- Celery 3.1.x
- Celery 4.0.x

Patch the celery library to trace task method calls::

    import celery
    from ddtrace.contrib.celery import patch; patch()

    app = celery.Celery()

    @app.task
    def my_task():
        pass


    class MyTask(app.Task):
        def run(self):
            pass


You may also manually patch celery apps or tasks for tracing::

    import celery
    from ddtrace.contrib.celery import patch_app, patch_task

    app = celery.Celery()
    app = patch_app(app)

    @app.task
    def my_task():
        pass

    # We don't have to patch this task since we patched `app`,
    # but we could patch a single task like this if we wanted to
    my_task = patch_task(my_task)


    class MyTask(celery.Task):
        def run(self):
            pass

    MyTask = patch_task(MyTask)
"""

from ..util import require_modules

required_modules = ['celery']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .app import patch_app, unpatch_app
        from .patch import patch, unpatch
        from .task import patch_task, unpatch_task
        __all__ = ['patch', 'patch_app', 'patch_task', 'unpatch', 'unpatch_app', 'unpatch_task']
