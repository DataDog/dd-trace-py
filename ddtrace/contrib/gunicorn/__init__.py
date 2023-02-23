"""
ddtrace works with Gunicorn.

.. note::
    If your Gunicorn server does not run inside of ``ddtrace-run``,
    be sure to ``import ddtrace.auto`` as early as possible in your application's
    lifecycle.
"""


def patch():
    pass


def unpatch():
    pass
