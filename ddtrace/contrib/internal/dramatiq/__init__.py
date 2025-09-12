"""
Enabling
~~~~~~~~
The dramatiq integration will trace background tasks as marked by the @dramatiq.actor
decorator. To trace your dramatiq app, call the patch method::

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
