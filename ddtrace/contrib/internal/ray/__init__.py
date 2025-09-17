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
- ``DD_TRACE_RAY_REGISTER_LONG_RUNNING_THRESHOLD``: Maximum span duration before it is considered
    long-runnning (default: ``10.0``seconds)

Notes
~~~~~

- The integration disables Ray's built-in OpenTelemetry tracing to avoid duplicate telemetry.
- The integration filters out non-Ray dashboard spans to reduce noise.
- Actor methods like ``ping`` and ``_polling`` are excluded from tracing to reduce noise.
"""

import os


def in_ray_job():
    # type: () -> bool
    """Returns whether we are in a ray environemt.
    This is accomplished by checking if the _RAY_SUBMISSION_ID environment variable is defined
    which means a job has been submitted and is traced
    """
    return bool(os.environ.get("_RAY_SUBMISSION_ID", False))
