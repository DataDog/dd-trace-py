"""
Some utils used by the dogtrace valkey integration
"""

from contextlib import contextmanager
from typing import Optional

from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.cache import ValkeyCommandEvent
from ddtrace.ext import valkey as valkeyx
from ddtrace.internal import core
from ddtrace.internal.utils.formats import stringify_cache_args


format_command_args = stringify_cache_args


def _pipeline_len(instance: object) -> Optional[int]:
    for attr in ("_command_stack", "command_stack"):
        if hasattr(instance, attr):
            return len(getattr(instance, attr))
    return None


@contextmanager
def _instrument_valkey_cmd(config_integration, instance, args):
    query = stringify_cache_args(args, cmd_max_len=config_integration.cmd_max_length)
    with core.context_with_event(
        ValkeyCommandEvent(
            component=config_integration.integration_name,
            integration_config=config_integration,
            resource=query.split(" ")[0] if config_integration.resource_only_command else query,
            service=trace_utils.ext_service(None, config_integration),
            cache_provider=valkeyx.APP,
            command_tag_name=valkeyx.CMD,
            db_system=valkeyx.APP,
            query=query,
            args_len=len(args),
            connection_provider=instance if hasattr(instance, "connection_pool") else None,
        )
    ) as ctx:
        yield ctx


@contextmanager
def _instrument_valkey_execute_pipeline(config_integration, cmds, instance, is_cluster=False):
    cmd_string = resource = "\n".join(cmds)
    if config_integration.resource_only_command:
        resource = "\n".join([cmd.split(" ")[0] for cmd in cmds])

    with core.context_with_event(
        ValkeyCommandEvent(
            component=config_integration.integration_name,
            integration_config=config_integration,
            resource=resource,
            service=trace_utils.ext_service(None, config_integration),
            cache_provider=valkeyx.APP,
            command_tag_name=valkeyx.CMD,
            db_system=valkeyx.APP,
            query=cmd_string,
            pipeline_len=_pipeline_len(instance),
            connection_provider=instance if not is_cluster and hasattr(instance, "connection_pool") else None,
        )
    ) as ctx:
        yield ctx.span


@contextmanager
def _instrument_valkey_execute_async_cluster_pipeline(config_integration, cmds, instance):
    cmd_string = resource = "\n".join(cmds)
    if config_integration.resource_only_command:
        resource = "\n".join([cmd.split(" ")[0] for cmd in cmds])

    with core.context_with_event(
        ValkeyCommandEvent(
            component=config_integration.integration_name,
            integration_config=config_integration,
            resource=resource,
            service=trace_utils.ext_service(None, config_integration),
            cache_provider=valkeyx.APP,
            command_tag_name=valkeyx.CMD,
            db_system=valkeyx.APP,
            query=cmd_string,
            pipeline_len=_pipeline_len(instance),
            connection_provider=instance if hasattr(instance, "connection_pool") else None,
        )
    ) as ctx:
        yield ctx.span
