"""
Enabling
~~~~~~~~
The dramatiq integration traces the enqueuing of background tasks. It creates a
span each time a task is sent to the broker via the ``send()`` or
``send_with_options()`` methods on an actor. The span measures the duration of the
enqueue call itself, not the execution time of the background task.

Use :func:`patch()<ddtrace.patch>` to manually enable the integration::

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
