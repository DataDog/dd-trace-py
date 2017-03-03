"""
The Celery integration will trace all tasks that are executed in the
background. To trace your Celery application, call the patch method::

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


If you don't need to patch all Celery tasks, you can patch individual
applications or tasks using a fine grain patching method::

    import celery
    from ddtrace.contrib.celery import patch_app, patch_task

    # patch only this application
    app = celery.Celery()
    app = patch_app(app)

    # or if you didn't patch the whole application, just patch
    # a single function or class based Task
    @app.task
    def fn_task():
        pass


    class BaseClassTask(celery.Task):
        def run(self):
            pass


    BaseClassTask = patch_task(BaseClassTask)
    fn_task = patch_task(fn_task)
"""
from ..util import require_modules


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
