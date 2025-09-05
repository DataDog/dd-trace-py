"""
The Ray integration traces Ray jobs, tasks (remote functions), and actor method executions.

All traces submitted from the Ray integration are tagged by:

- ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.

The service for Ray spans is set to the Ray job submission id to group telemetry per job.

Enabling
~~~~~~~~

The Ray integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.
Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the Ray integration::

    from ddtrace import patch
    patch(ray=True)


Supported Versions
~~~~~~~~~~~~~~~~~~

Ray ``>=2.46.0``


What is traced
~~~~~~~~~~~~~~

- Job submission and completion (``ray.job.submit``, ``ray.job``)
- Task submission and execution (``func.remote()``, the function body)
- Actor method submission and execution (``Actor.method.remote()``, the method body)


Notes
~~~~~

- The integration disables Ray's built-in OpenTelemetry tracing to avoid duplicate telemetry.
"""
import os


def in_ray_job():
    # type: () -> bool
    """Returns whether we are in a ray environemt.
    This is accomplished by checking if the _RAY_SUBMISSION_ID environment variable is defined
    which means a job has been submitted and is traced
    """
    return bool(os.environ.get("_RAY_SUBMISSION_ID", False))
