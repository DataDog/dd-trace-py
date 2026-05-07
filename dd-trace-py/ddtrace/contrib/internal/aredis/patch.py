import aredis
import wrapt

from ddtrace import config
from ddtrace.contrib.internal.redis_utils import _instrument_redis_cmd
from ddtrace.contrib.internal.redis_utils import _instrument_redis_execute_pipeline
from ddtrace.contrib.internal.redis_utils import _run_redis_command_async
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import CMD_MAX_LEN
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import stringify_cache_args
from ddtrace.internal.utils.wrappers import unwrap


config._add(
    "aredis",
    dict(
        _default_service=schematize_service_name("redis"),
        cmd_max_length=int(env.get("DD_AREDIS_CMD_MAX_LENGTH", CMD_MAX_LEN)),
        resource_only_command=asbool(env.get("DD_REDIS_RESOURCE_ONLY_COMMAND", True)),
    ),
)


def get_version() -> str:
    return getattr(aredis, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"aredis": "*"}


def patch():
    """Patch the instrumented methods"""
    if getattr(aredis, "_datadog_patch", False):
        return
    aredis._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    _w("aredis.client", "StrictRedis.execute_command", traced_execute_command)
    _w("aredis.pipeline", "StrictPipeline.execute", traced_execute_pipeline)
    _w("aredis.pipeline", "StrictPipeline.immediate_execute_command", traced_execute_command)


def unpatch():
    if getattr(aredis, "_datadog_patch", False):
        aredis._datadog_patch = False

        unwrap(aredis.client.StrictRedis, "execute_command")
        unwrap(aredis.pipeline.StrictPipeline, "execute")
        unwrap(aredis.pipeline.StrictPipeline, "immediate_execute_command")


#
# tracing functions
#
async def traced_execute_command(func, instance, args, kwargs):
    with _instrument_redis_cmd(config.aredis, instance, args) as ctx:
        return await _run_redis_command_async(ctx=ctx, func=func, args=args, kwargs=kwargs)


async def traced_execute_pipeline(func, instance, args, kwargs):
    cmds = [stringify_cache_args(c, cmd_max_len=config.aredis.cmd_max_length) for c, _ in instance.command_stack]
    with _instrument_redis_execute_pipeline(config.aredis, cmds, instance):
        return await func(*args, **kwargs)
