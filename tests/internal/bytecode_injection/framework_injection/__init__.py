"""
The bytecode injection is used used to instrument all the functions
within a codebase during the test runs. This is to ensure that the tests
still pass with the instrumentation on.

To run an bytecode injection test, set the environment variable

    PYTHONPATH=/path/to/tests/internal/bytecode_injection/injection/

As a proof of work for the instrumentation, we inject a callback for each line.
When the callback is called it will registered the file and the line that called
him.
"""
