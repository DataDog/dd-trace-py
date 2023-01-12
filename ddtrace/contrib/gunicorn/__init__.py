"""
``ddtrace`` supports `Gunicorn <https://gunicorn.org>`__.

If the application is using the ``gevent`` worker class, ``gevent`` monkey patching must be performed before loading the
``ddtrace`` library.

There are different options to ensure this happens:

- If using ``ddtrace-run``, set the environment variable ``DD_GEVENT_PATCH_ALL=1``.

- Replace ``ddtrace-run`` by using ``import ddtrace.bootstrap.sitecustomize`` as the first import of the application.

- Use a `post_worker_init <https://docs.gunicorn.org/en/stable/settings.html#post-worker-init>`_
  hook to import ``ddtrace.bootstrap.sitecustomize``.
"""


def patch():
    pass


def unpatch():
    pass
