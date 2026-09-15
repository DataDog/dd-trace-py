# Temporal tracing sample

This sample uses Temporal's public Python APIs to start a workflow, query its
state, signal it, and execute an activity. Start a local Temporal development
server, then run the worker and starter in separate terminals with automatic
instrumentation enabled:

```console
$ temporal server start-dev
$ ddtrace-run python -m tests.contrib.temporalio.sample_app.worker
$ ddtrace-run python -m tests.contrib.temporalio.sample_app.starter
```

The completed workflow returns `Welcome, Temporal!`. Its trace contains a
`temporal.start_workflow` producer span connected to a
`temporal.run_activity` consumer span. The query and signal calls create
`temporal.query_workflow` and `temporal.signal_workflow` spans.
