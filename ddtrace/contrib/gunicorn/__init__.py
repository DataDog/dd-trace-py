"""
**Note:** ``ddtrace-run`` is not supported with `Gunicorn <https://gunicorn.org>`__.

``ddtrace`` only supports Gunicorn when configured as follows:

- `ddtrace-run` is not used
- The `DD_GEVENT_PATCH_ALL=1` environment variable is set
- Gunicorn's ```post_fork`` <https://docs.gunicorn.org/en/stable/settings.html#post-fork>`__ hook does not attempt to start any threads
- ``import ddtrace.bootstrap.sitecustomize`` must be called either in the application's main process or in the ```post_worker_init`` <https://docs.gunicorn.org/en/stable/settings.html#post-worker-init>`__ hook.
"""


def patch():
    pass


def unpatch():
    pass
