threading
~~~~~~~~~

This benchmark test is used to simulate the creation, encoding, and flushing of traces in threaded environments.

It uses a ``concurrent.futures.ThreadPool`` to manage the total number of workers.

The only modification to the tracing workflow that has been made is using a ``NoopWriter`` which does not start a
background thread and drops traces on ``writer.write``. This means we skip encoding, queuing, and flushing payloads
to the agent, but we will still use the span processors.
