Benchmark: threading
====================

This benchmark test is used to simulate the creation, encoding, and flushing of traces in threaded environments.

It uses a ``concurrent.futures.ThreadPool`` to manage the total number of workers.

The only modification to the tracing workflow that has been modified is the ``AgentWriter``'s ``_send_payload`` has been overridden to do nothing.
This means we will still start the background worker for the agent, put traces into the queue, and encode them into payloads to send. We will just
not try to make the HTTP call to the agent to reduce any impact from an external process on this test.
