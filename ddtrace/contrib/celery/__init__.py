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


To change Celery service name, you can update the attached ``Pin``
instance::

    from ddtrace import Pin

    app = celery.Celery()

    @app.task
    def compute_stats():
        pass

    # globally
    Pin.override(app, service='background-jobs')

    # by task
    Pin.override(compute_stats, service='data-processing')


By default, reported service names are:
    * ``celery-producer`` when messages are sent for processing
    * ``celery-worker`` when messages are processed

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
