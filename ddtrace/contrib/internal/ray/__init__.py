"""
The ray integration traces:
 - Job lifetime (job submit, job run)
 - Task submission and execution
 - Actor method submission and execution

Enabling
~~~~~~~~

The Ray integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the Ray integration::

    from ddtrace import patch
    patch(ray=True)

The recommended way to instrument Ray, is to instrument the Ray cluster.
You can do it by starting the Ray head with a tracing startup hook::

    ray start --head --tracing-startup-hook=ddtrace.contrib.ray:setup_tracing


Configuration
~~~~~~~~~~~~~

The Ray integration can be configured using environment variables:

- ``DD_TRACE_RAY_RESUBMIT_LONG_RUNNING_INTERVAL``: Interval for resubmitting long-running
    spans (default: ``120.0`` seconds)

Ray service name can be configured by:

- specifying in submission ID using ``job:your-job-name`` during job submission::

    ray job submit --submission-id="job:my_model,run:39" -- python entrypoint.py

- specifying in metadata during job submission::

    ray job submit --metadata-json='{"job_name": "my_model"}' -- python entrypoint.py

- specifying ``DD_SERVICE`` when initializing your Ray cluster.

By default, the service name will be the name of your entrypoint

Notes
~~~~~

- The integration disables Ray's built-in OpenTelemetry tracing to avoid duplicate telemetry.
- Actor methods like ``ping`` and ``_polling`` are excluded from tracing to reduce noise.
"""
