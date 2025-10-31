"""
The ray integration traces:
 - Job lifetime (job submit, job run)
 - Task submission and execution
 - Actor method submission and execution

Enabling
~~~~~~~~

Ray instrumentation is experimental. It is deactivated by default. To enable it,
you have to follow one of the two methods below:

The recommended way to instrument Ray, is to instrument the Ray cluster.
You can do it by starting the Ray head with a tracing startup hook::

    ray start --head --tracing-startup-hook=ddtrace.contrib.ray:setup_tracing

Otherwise, you can specify the tracing hook in `ray.init()` using::

    ray.init(_tracing_startup_hook="ddtrace.contrib.ray:setup_tracing")

Note that this method does not provide full tracing capabilities.


Configuration
~~~~~~~~~~~~~

The Ray integration can be configured using environment variables:

- ``DD_TRACE_RAY_CORE_API``: Enable tracing of Ray's core API functions like ``ray.wait()``
    (default: ``False``)

- ``DD_TRACE_RAY_ARGS_KWARGS``: Enable tracing of arguments and keyword arguments passed to
    Ray tasks and actor methods (default: ``False``)

- ``DD_TRACE_EXPERIMENTAL_LONG_RUNNING_FLUSH_INTERVAL``: Interval for resubmitting long-running
    spans (default: ``120.0`` seconds)

- ``DD_TRACE_RAY_USE_ENTRYPOINT_AS_SERVICE_NAME``: Whether to use the job entrypoint as the
    service name (default: ``False``). If ``True``, the entrypoint will be used as the service
    name if DD_SERVICE is not set and a job name is not specified in the metadata.

- ``DD_TRACE_RAY_REDACT_ENTRYPOINT_PATHS``: Whether to redact file paths in the job entrypoint
    (default: ``True``). If ``True``, file paths in the entrypoint will be redacted to avoid
    leaking sensitive information.

Ray service name can be configured by:

- specifying in submission ID using ``job:your-job-name`` during job submission::

    ray job submit --submission-id="job:my_model,run:39" -- python entrypoint.py

- specifying in metadata during job submission::

    ray job submit --metadata-json='{"job_name": "my_model"}' -- python entrypoint.py

- specifying ``DD_SERVICE`` when initializing your Ray cluster.

- setting ``DD_TRACE_RAY_USE_ENTRYPOINT_AS_SERVICE_NAME=True``. In this case, the service
  name will be the name of your entrypoint script.

By default, the service name will be ``unnamed.ray.job``.

Notes
~~~~~

- The integration disables Ray's built-in OpenTelemetry tracing to avoid duplicate telemetry.
- Actor methods like ``ping`` and ``_polling`` are excluded from tracing to reduce noise.
"""
