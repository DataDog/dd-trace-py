"""
Enabling
~~~~~~~~
The dramatiq integration will trace background tasks as marked by the @dramatiq.actor
decorator. To trace your dramatiq app, call the patch method:

    import dramatiq
    from ddtrace import patch

    patch(dramatiq=True)

    @dramatiq.actor
    def my_background_task():
        # do something

    @dramatiq.actor
    def my_other_task(content):
        # do something

    if __name__ == "__main__":
        my_background_task.send()
        my_other_task.send("mycontent")
        # Can also call the methods with options
        # my_other_task.send_with_options(("mycontent"), {"max_retries"=3})

You may also enable dramatiq tracing automatically via ddtrace-run::

    ddtrace-run python app.py

"""


# Required to allow users to import from  `ddtrace.contrib.dramatiq.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.dramatiq.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.dramatiq.patch import patch  # noqa: F401
from ddtrace.contrib.internal.dramatiq.patch import unpatch  # noqa: F401
