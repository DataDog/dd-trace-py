import contextlib
import os


@contextlib.contextmanager
def override_env(env):
    """
    Temporarily override ``os.environ`` with provided values::

        >>> with self.override_env(dict(DATADOG_TRACE_DEBUG=True)):
            # Your test
    """
    # Copy the full original environment
    original = dict(os.environ)

    # Update based on the passed in arguments
    os.environ.update(env)
    try:
        yield
    finally:
        # Full clear the environment out and reset back to the original
        os.environ.clear()
        os.environ.update(original)
