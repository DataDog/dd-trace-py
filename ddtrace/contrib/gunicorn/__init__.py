"""
**Note:** ``ddtrace-run`` and Python 2 are both not supported with `Gunicorn <https://gunicorn.org>`__.

``ddtrace`` only supports Gunicorn's ``gevent`` worker type when configured as follows:

- The application is running under a Python version >=3.6 and <=3.10
- `ddtrace-run` is not used
- The `DD_GEVENT_PATCH_ALL=1` environment variable is set
- Gunicorn's ```post_fork`` <https://docs.gunicorn.org/en/stable/settings.html#post-fork>`__ hook does not import from
  ``ddtrace``
- ``import ddtrace.bootstrap.sitecustomize`` is called either in the application's main process or in the
  ```post_worker_init`` <https://docs.gunicorn.org/en/stable/settings.html#post-worker-init>`__ hook.

.. code-block:: python

  # gunicorn.conf.py
  def post_fork(server, worker):
      # don't touch ddtrace here
      pass

  def post_worker_init(worker):
      import ddtrace.bootstrap.sitecustomize

  workers = 4
  worker_class = "gevent"
  bind = "8080"

.. code-block:: bash

  DD_GEVENT_PATCH_ALL=1 gunicorn --config gunicorn.conf.py path.to.my:app
"""


def patch():
    pass


def unpatch():
    pass
