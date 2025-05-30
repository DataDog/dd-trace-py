"""
The valkey integration traces valkey requests.


Enabling
~~~~~~~~

The valkey integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(valkey=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.valkey["service"]

   The service name reported by default for valkey traces.

   This option can also be set with the ``DD_VALKEY_SERVICE`` environment
   variable.

   Default: ``"valkey"``


.. py:data:: ddtrace.config.valkey["cmd_max_length"]

   Max allowable size for the valkey command span tag.
   Anything beyond the max length will be replaced with ``"..."``.

   This option can also be set with the ``DD_VALKEY_CMD_MAX_LENGTH`` environment
   variable.

   Default: ``1000``


.. py:data:: ddtrace.config.valkey["resource_only_command"]

   The span resource will only include the command executed. To include all
   arguments in the span resource, set this value to ``False``.

   This option can also be set with the ``DD_VALKEY_RESOURCE_ONLY_COMMAND`` environment
   variable.

   Default: ``True``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure particular valkey instances use the :class:`Pin <ddtrace.Pin>` API::

    import valkey
    from ddtrace.trace import Pin

    client = valkey.StrictValkey(host="localhost", port=6379)

    # Override service name for this instance
    Pin.override(client, service="my-custom-queue")

    # Traces reported for this client will now have "my-custom-queue"
    # as the service name.
    client.get("my-key")
"""
import os
from typing import Dict

import valkey
import wrapt

from ddtrace import config
from ddtrace._trace.utils_valkey import _instrument_valkey_cmd
from ddtrace._trace.utils_valkey import _instrument_valkey_execute_pipeline
from ddtrace.contrib.internal.valkey_utils import ROW_RETURNING_COMMANDS
from ddtrace.contrib.internal.valkey_utils import determine_row_count
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import CMD_MAX_LEN
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import stringify_cache_args
from ddtrace.trace import Pin


config._add(
    "valkey",
    {
        "_default_service": schematize_service_name("valkey"),
        "cmd_max_length": int(os.getenv("DD_VALKEY_CMD_MAX_LENGTH", CMD_MAX_LEN)),
        "resource_only_command": asbool(os.getenv("DD_VALKEY_RESOURCE_ONLY_COMMAND", True)),
    },
)


def get_version():
    # type: () -> str
    return getattr(valkey, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"valkey": ">=6.0.0"}


def patch():
    """Patch the instrumented methods

    This duplicated doesn't look nice. The nicer alternative is to use an ObjectProxy on top
    of Valkey and StrictValkey. However, it means that any "import valkey.Valkey" won't be instrumented.
    """
    if getattr(valkey, "_datadog_patch", False):
        return
    valkey._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    from .asyncio_patch import instrumented_async_execute_cluster_pipeline
    from .asyncio_patch import instrumented_async_execute_command
    from .asyncio_patch import instrumented_async_execute_pipeline

    _w("valkey", "Valkey.execute_command", instrumented_execute_command(config.valkey))
    _w("valkey", "Valkey.pipeline", instrumented_pipeline)
    _w("valkey.client", "Pipeline.execute", instrumented_execute_pipeline(config.valkey, False))
    _w("valkey.client", "Pipeline.immediate_execute_command", instrumented_execute_command(config.valkey))
    _w("valkey.cluster", "ValkeyCluster.execute_command", instrumented_execute_command(config.valkey))
    _w("valkey.cluster", "ValkeyCluster.pipeline", instrumented_pipeline)
    _w("valkey.cluster", "ClusterPipeline.execute", instrumented_execute_pipeline(config.valkey, True))
    Pin(service=None).onto(valkey.cluster.ValkeyCluster)

    _w("valkey.asyncio.client", "Valkey.execute_command", instrumented_async_execute_command)
    _w("valkey.asyncio.client", "Valkey.pipeline", instrumented_pipeline)
    _w("valkey.asyncio.client", "Pipeline.execute", instrumented_async_execute_pipeline)
    _w("valkey.asyncio.client", "Pipeline.immediate_execute_command", instrumented_async_execute_command)
    Pin(service=None).onto(valkey.asyncio.Valkey)

    _w("valkey.asyncio.cluster", "ValkeyCluster.execute_command", instrumented_async_execute_command)
    _w("valkey.asyncio.cluster", "ValkeyCluster.pipeline", instrumented_pipeline)
    _w("valkey.asyncio.cluster", "ClusterPipeline.execute", instrumented_async_execute_cluster_pipeline)
    Pin(service=None).onto(valkey.asyncio.ValkeyCluster)

    Pin(service=None).onto(valkey.StrictValkey)


def unpatch():
    if getattr(valkey, "_datadog_patch", False):
        valkey._datadog_patch = False

        unwrap(valkey.Valkey, "execute_command")
        unwrap(valkey.Valkey, "pipeline")
        unwrap(valkey.client.Pipeline, "execute")
        unwrap(valkey.client.Pipeline, "immediate_execute_command")
        unwrap(valkey.cluster.ValkeyCluster, "execute_command")
        unwrap(valkey.cluster.ValkeyCluster, "pipeline")
        unwrap(valkey.cluster.ClusterPipeline, "execute")
        unwrap(valkey.asyncio.client.Valkey, "execute_command")
        unwrap(valkey.asyncio.client.Valkey, "pipeline")
        unwrap(valkey.asyncio.client.Pipeline, "execute")
        unwrap(valkey.asyncio.client.Pipeline, "immediate_execute_command")
        unwrap(valkey.asyncio.cluster.ValkeyCluster, "execute_command")
        unwrap(valkey.asyncio.cluster.ValkeyCluster, "pipeline")
        unwrap(valkey.asyncio.cluster.ClusterPipeline, "execute")


def _run_valkey_command(ctx: core.ExecutionContext, func, args, kwargs):
    parsed_command = stringify_cache_args(args)
    valkey_command = parsed_command.split(" ")[0]
    rowcount = None
    result = None
    try:
        result = func(*args, **kwargs)
        return result
    except Exception:
        rowcount = 0
        raise
    finally:
        if rowcount is None:
            rowcount = determine_row_count(valkey_command=valkey_command, result=result)
        if valkey_command not in ROW_RETURNING_COMMANDS:
            rowcount = None
        core.dispatch("valkey.command.post", [ctx, rowcount])


#
# tracing functions
#
def instrumented_execute_command(integration_config):
    def _instrumented_execute_command(func, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        with _instrument_valkey_cmd(pin, integration_config, instance, args) as ctx:
            return _run_valkey_command(ctx=ctx, func=func, args=args, kwargs=kwargs)

    return _instrumented_execute_command


def instrumented_pipeline(func, instance, args, kwargs):
    pipeline = func(*args, **kwargs)
    pin = Pin.get_from(instance)
    if pin:
        pin.onto(pipeline)
    return pipeline


def instrumented_execute_pipeline(integration_config, is_cluster=False):
    def _instrumented_execute_pipeline(func, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        if is_cluster:
            cmds = [
                stringify_cache_args(c.args, cmd_max_len=integration_config.cmd_max_length)
                for c in instance.command_stack
            ]
        else:
            cmds = [
                stringify_cache_args(c, cmd_max_len=integration_config.cmd_max_length)
                for c, _ in instance.command_stack
            ]
        with _instrument_valkey_execute_pipeline(pin, integration_config, cmds, instance, is_cluster):
            return func(*args, **kwargs)

    return _instrumented_execute_pipeline
