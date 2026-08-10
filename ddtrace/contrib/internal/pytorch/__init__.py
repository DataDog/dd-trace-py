"""
The pytorch integration traces PyTorch distributed training jobs.

Always-on: a single long-lived ``pytorch.rank`` span is emitted per rank.
Tags: ``rank``, ``world_size``, ``framework`` (DDP / FSDP / DeepSpeed),
``launcher``, ``torch.distributed.backend``, ``training_job_id``
(auto-resolved from ``RAY_JOB_ID``, ``TORCHELASTIC_RUN_ID``,
``KUBEFLOW_TRAINING_JOB_ID``, ``SLURM_JOB_ID``, or a per-rank UUID),
and Ray Train run context when running under Ray Train.


Enabling
~~~~~~~~

The PyTorch integration is **opt-in**. Enable explicitly via::

    DD_PATCH_MODULES=pytorch:true

or programmatically::

    import ddtrace
    ddtrace.patch(pytorch=True)


Global configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pytorch["service"]

   The service name reported by default for pytorch spans.

   This option can also be set with the ``DD_PYTORCH_SERVICE`` environment variable.

   Default: ``"pytorch"``

"""

from ddtrace import config


config._add(  # type: ignore[no-untyped-call]
    "pytorch",
    {
        "_default_service": "pytorch",
    },
)
