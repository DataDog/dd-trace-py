# 3p
import rediscluster
import wrapt

# project
from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.redis.patch import instrumented_execute_command
from ddtrace.contrib.internal.trace_utils import set_service_and_source
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import redis as redisx
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cache_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import CMD_MAX_LEN
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import stringify_cache_args
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.trace import tracer


# DEV: In `2.0.0` `__version__` is a string and `VERSION` is a tuple,
#      but in `1.x.x` `__version__` is a tuple and `VERSION` does not exist
REDISCLUSTER_VERSION = getattr(rediscluster, "VERSION", rediscluster.__version__)

config._add(
    "rediscluster",
    dict(
        _default_service=schematize_service_name("rediscluster"),
        cmd_max_length=int(env.get("DD_REDISCLUSTER_CMD_MAX_LENGTH", CMD_MAX_LEN)),
        resource_only_command=asbool(env.get("DD_REDIS_RESOURCE_ONLY_COMMAND", True)),
    ),
)


def get_version() -> str:
    return getattr(rediscluster, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"rediscluster": ">=2.0"}


def patch():
    """Patch the instrumented methods"""
    if getattr(rediscluster, "_datadog_patch", False):
        return
    rediscluster._datadog_patch = True

    _w = wrapt.wrap_function_wrapper
    if REDISCLUSTER_VERSION >= (2, 0, 0):
        _w("rediscluster", "client.RedisCluster.execute_command", instrumented_execute_command(config.rediscluster))
        _w("rediscluster", "pipeline.ClusterPipeline.execute", traced_execute_pipeline)
    else:
        _w("rediscluster", "StrictRedisCluster.execute_command", instrumented_execute_command(config.rediscluster))
        _w("rediscluster", "StrictClusterPipeline.execute", traced_execute_pipeline)


def unpatch():
    if getattr(rediscluster, "_datadog_patch", False):
        rediscluster._datadog_patch = False

        if REDISCLUSTER_VERSION >= (2, 0, 0):
            unwrap(rediscluster.client.RedisCluster, "execute_command")
            unwrap(rediscluster.pipeline.ClusterPipeline, "execute")
        else:
            unwrap(rediscluster.StrictRedisCluster, "execute_command")
            unwrap(rediscluster.StrictClusterPipeline, "execute")


#
# tracing functions
#


def traced_execute_pipeline(func, instance, args, kwargs):
    cmds = [
        stringify_cache_args(c.args, cmd_max_len=config.rediscluster.cmd_max_length) for c in instance.command_stack
    ]
    resource = "\n".join(cmds)
    with tracer.trace(
        schematize_cache_operation(redisx.CMD, cache_provider=redisx.APP),
        resource=resource,
        span_type=SpanTypes.REDIS,
    ) as s:
        set_service_and_source(
            s, trace_utils.ext_service(None, config.rediscluster, "rediscluster"), config.rediscluster
        )
        s._set_attribute(SPAN_KIND, SpanKind.CLIENT)
        s._set_attribute(COMPONENT, config.rediscluster.integration_name)
        s._set_attribute(db.SYSTEM, redisx.APP)
        s._set_attribute(_SPAN_MEASURED_KEY, 1)
        s._set_attribute(redisx.RAWCMD, resource)
        s._set_attribute(redisx.PIPELINE_LEN, len(instance.command_stack))

        return func(*args, **kwargs)
