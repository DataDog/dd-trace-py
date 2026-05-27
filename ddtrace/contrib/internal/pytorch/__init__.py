"""
The pytorch integration traces PyTorch distributed training jobs.

Layer Zero (always-on default) emits a single long-lived ``pytorch.rank``
span per rank plus device-tagged DogStatsD distributions for collective
duration, bytes, and per-second rate. Layer One (opt-in via
``DD_PYTORCH_COLLECTIVE_TRACE=true``) additionally emits a span per
collective (``all_reduce``, ``all_gather``, ``broadcast``, ``reduce_scatter``,
``barrier``, ``all_gather_into_tensor``, ``reduce_scatter_tensor``) plus
DDP gradient comm hooks, FSDP forward, DeepSpeed engine methods, and
optimizer step boundaries. All ranks are correlated by a shared ``job_id``.


Enabling
~~~~~~~~

The PyTorch integration is **opt-in**. Enable explicitly via::

    DD_PATCH_MODULES=pytorch:true

or programmatically::

    import ddtrace
    ddtrace.patch(pytorch=True)

Even when enabled, ``install()`` is a no-op on processes that do not look
like distributed training. The integration installs its wrappers only when
ONE of the following is true:

- ``RANK`` or ``WORLD_SIZE`` environment variable is set.
- ``torch.distributed.init_process_group()`` has already been called.
- ``DD_PYTORCH_FORCE_INSTALL=true`` is set.


Global configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pytorch["service"]

   The service name reported by default for pytorch spans.

   This option can also be set with the ``DD_PYTORCH_SERVICE`` environment variable.

   Default: ``"pytorch"``


Environment variables
~~~~~~~~~~~~~~~~~~~~~

``DD_PYTORCH_JOB_ID``
    Manual override for the cross-rank job identifier. When unset, the
    integration walks ``RAY_JOB_ID``, ``TORCHELASTIC_RUN_ID``,
    ``KUBEFLOW_TRAINING_JOB_ID``, then ``SLURM_JOB_ID``. If none of
    these are set, a per-rank UUID is used internally but
    ``training_job.id`` is NOT stamped on spans — set this variable
    to enable cross-rank trace correlation.

``DD_PYTORCH_GRAD_COMM``
    Set to ``false`` to disable DDP comm-hook registration entirely.
    Default: ``true``.

``DD_PYTORCH_COLLECTIVE_TRACE``
    Set to ``true`` to enable per-collective span emission (Layer One).
    Default: ``false``.

``DD_PYTORCH_PROFILING``
    Set to ``true`` to enable Layer Two step profiling
    (``pytorch.step`` / ``pytorch.forward`` / ``pytorch.backward`` /
    ``pytorch.data_load`` spans). Default: ``false``.

``DD_PYTORCH_KERNEL_PROFILING``
    Set to ``true`` to enable Layer Three CUDA kernel profiling on the
    designated rank. Requires ``DD_PYTORCH_PROFILING=true``.
    Default: ``false``.

``DD_PYTORCH_RATE_TICKER_INTERVAL_S``
    Seconds between RateTicker emissions of ``collective.calls_per_sec``
    and ``collective.bytes_per_sec``. Default 1.0. Lower (e.g., 0.1)
    for debug-tier resolution at 10× the DogStatsD volume.

``DD_PYTORCH_FORCE_INSTALL``
    Set to ``true`` to install wrappers even when no distributed-launcher
    signals are present (no ``RANK``/``WORLD_SIZE`` env, no prior
    ``init_process_group``). Useful for custom launchers.

"""

from ddtrace import config
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


# AIDEV-NOTE: Pop first — importlib.reload otherwise keeps stale env defaults.
config._integration_configs.pop("pytorch", None)
config._add(
    "pytorch",
    {
        "_default_service": "pytorch",
        "service": env.get("DD_PYTORCH_SERVICE"),
        "grad_comm_enabled": asbool(env.get("DD_PYTORCH_GRAD_COMM", "true")),
        "collective_trace_enabled": asbool(env.get("DD_PYTORCH_COLLECTIVE_TRACE", "false")),
        "rate_ticker_interval_s": float(env.get("DD_PYTORCH_RATE_TICKER_INTERVAL_S", "1.0") or "1.0"),
    },
)
