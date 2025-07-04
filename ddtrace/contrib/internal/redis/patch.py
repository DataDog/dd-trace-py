import os
from typing import Dict

import redis
import wrapt

from ddtrace import config
from ddtrace._trace.utils_redis import _instrument_redis_cmd
from ddtrace._trace.utils_redis import _instrument_redis_execute_pipeline
from ddtrace.contrib.internal.redis_utils import ROW_RETURNING_COMMANDS
from ddtrace.contrib.internal.redis_utils import determine_row_count
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import CMD_MAX_LEN
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import stringify_cache_args
from ddtrace.trace import Pin


config._add(
    "redis",
    {
        "_default_service": schematize_service_name("redis"),
        "cmd_max_length": int(os.getenv("DD_REDIS_CMD_MAX_LENGTH", CMD_MAX_LEN)),
        "resource_only_command": asbool(os.getenv("DD_REDIS_RESOURCE_ONLY_COMMAND", True)),
    },
)


def get_version():
    # type: () -> str
    return getattr(redis, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"redis": "*"}


def patch():
    """Patch the instrumented methods

    This duplicated doesn't look nice. The nicer alternative is to use an ObjectProxy on top
    of Redis and StrictRedis. However, it means that any "import redis.Redis" won't be instrumented.
    """
    if getattr(redis, "_datadog_patch", False):
        return
    redis._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    if redis.VERSION < (3, 0, 0):
        _w("redis", "StrictRedis.execute_command", instrumented_execute_command(config.redis))
        _w("redis", "StrictRedis.pipeline", instrumented_pipeline)
        _w("redis", "Redis.pipeline", instrumented_pipeline)
        _w("redis.client", "BasePipeline.execute", instrumented_execute_pipeline(config.redis, False))
        _w("redis.client", "BasePipeline.immediate_execute_command", instrumented_execute_command(config.redis))
    else:
        _w("redis", "Redis.execute_command", instrumented_execute_command(config.redis))
        _w("redis", "Redis.pipeline", instrumented_pipeline)
        _w("redis.client", "Pipeline.execute", instrumented_execute_pipeline(config.redis, False))
        _w("redis.client", "Pipeline.immediate_execute_command", instrumented_execute_command(config.redis))
        if redis.VERSION >= (4, 1):
            # Redis v4.1 introduced support for redis clusters and rediscluster package was deprecated.
            # https://github.com/redis/redis-py/commit/9db1eec71b443b8e7e74ff503bae651dc6edf411
            _w("redis.cluster", "RedisCluster.execute_command", instrumented_execute_command(config.redis))
            _w("redis.cluster", "RedisCluster.pipeline", instrumented_pipeline)
            _w("redis.cluster", "ClusterPipeline.execute", instrumented_execute_pipeline(config.redis, True))
            Pin(service=None).onto(redis.cluster.RedisCluster)
        # Avoid mypy invalid syntax errors when parsing Python 2 files
        if redis.VERSION >= (4, 2, 0):
            from .asyncio_patch import instrumented_async_execute_command
            from .asyncio_patch import instrumented_async_execute_pipeline

            _w("redis.asyncio.client", "Redis.execute_command", instrumented_async_execute_command)
            _w("redis.asyncio.client", "Redis.pipeline", instrumented_pipeline)
            _w("redis.asyncio.client", "Pipeline.execute", instrumented_async_execute_pipeline)
            _w("redis.asyncio.client", "Pipeline.immediate_execute_command", instrumented_async_execute_command)
            Pin(service=None).onto(redis.asyncio.Redis)

        if redis.VERSION >= (4, 3, 0):
            from .asyncio_patch import instrumented_async_execute_command

            _w("redis.asyncio.cluster", "RedisCluster.execute_command", instrumented_async_execute_command)

            if redis.VERSION >= (4, 3, 2):
                from .asyncio_patch import instrumented_async_execute_cluster_pipeline

                _w("redis.asyncio.cluster", "RedisCluster.pipeline", instrumented_pipeline)
                _w("redis.asyncio.cluster", "ClusterPipeline.execute", instrumented_async_execute_cluster_pipeline)

            Pin(service=None).onto(redis.asyncio.RedisCluster)

    Pin(service=None).onto(redis.StrictRedis)


def unpatch():
    if getattr(redis, "_datadog_patch", False):
        redis._datadog_patch = False

        if redis.VERSION < (3, 0, 0):
            unwrap(redis.StrictRedis, "execute_command")
            unwrap(redis.StrictRedis, "pipeline")
            unwrap(redis.Redis, "pipeline")
            unwrap(redis.client.BasePipeline, "execute")
            unwrap(redis.client.BasePipeline, "immediate_execute_command")
        else:
            unwrap(redis.Redis, "execute_command")
            unwrap(redis.Redis, "pipeline")
            unwrap(redis.client.Pipeline, "execute")
            unwrap(redis.client.Pipeline, "immediate_execute_command")
            if redis.VERSION >= (4, 1, 0):
                unwrap(redis.cluster.RedisCluster, "execute_command")
                unwrap(redis.cluster.RedisCluster, "pipeline")
                unwrap(redis.cluster.ClusterPipeline, "execute")
            if redis.VERSION >= (4, 2, 0):
                unwrap(redis.asyncio.client.Redis, "execute_command")
                unwrap(redis.asyncio.client.Redis, "pipeline")
                unwrap(redis.asyncio.client.Pipeline, "execute")
                unwrap(redis.asyncio.client.Pipeline, "immediate_execute_command")
            if redis.VERSION >= (4, 3, 0):
                unwrap(redis.asyncio.cluster.RedisCluster, "execute_command")
            if redis.VERSION >= (4, 3, 2):
                unwrap(redis.asyncio.cluster.RedisCluster, "pipeline")
                unwrap(redis.asyncio.cluster.ClusterPipeline, "execute")


def _run_redis_command(ctx: core.ExecutionContext, func, args, kwargs):
    parsed_command = stringify_cache_args(args)
    redis_command = parsed_command.split(" ")[0]
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
            rowcount = determine_row_count(redis_command=redis_command, result=result)
        if redis_command not in ROW_RETURNING_COMMANDS:
            rowcount = None
        core.dispatch("redis.command.post", [ctx, rowcount])


#
# tracing functions
#
def instrumented_execute_command(integration_config):
    def _instrumented_execute_command(func, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        with _instrument_redis_cmd(pin, integration_config, instance, args) as ctx:
            return _run_redis_command(ctx=ctx, func=func, args=args, kwargs=kwargs)

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
        with _instrument_redis_execute_pipeline(pin, integration_config, cmds, instance, is_cluster):
            return func(*args, **kwargs)

    return _instrumented_execute_pipeline
