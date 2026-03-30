"""
The wandb integration traces Weights & Biases run lifecycle and logging.

Enabling
~~~~~~~~

The ``wandb`` integration is disabled by default. Enable it with
:ref:`ddtrace-run<ddtracerun>` using ``DD_PATCH_MODULES=wandb:true``, or call
:func:`patch() <ddtrace.patch>`.

When enabled, ``wandb.init`` is stubbed with a Datadog implementation that
creates a run span and returns a traced run object. Calls to ``run.log``
create ``wandb.log`` spans with top-level key/value tags.

Manual patching example::

    from ddtrace import patch
    patch(wandb=True)
    import wandb
    wandb.login()
    with wandb.init(project="my-project", config={"epochs": 10}) as run:
        run.log({"loss": 0.42})
"""
