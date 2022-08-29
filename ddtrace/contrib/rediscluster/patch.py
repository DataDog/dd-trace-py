# 3p
import rediscluster

# project
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib.redis.patch import traced_execute_command
from ddtrace.contrib.redis.patch import traced_pipeline
from ddtrace.ext import SpanTypes
from ddtrace.ext import redis as redisx
from ddtrace.internal.utils.formats import stringify_cache_args
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.pin import Pin
from ddtrace.vendor import wrapt

from .. import trace_utils


# DEV: In `2.0.0` `__version__` is a string and `VERSION` is a tuple,
#      but in `1.x.x` `__version__` is a tuple annd `VERSION` does not exist
REDISCLUSTER_VERSION = getattr(rediscluster, "VERSION", rediscluster.__version__)

config._add("rediscluster", dict(_default_service="rediscluster"))


def patch():
    """Patch the instrumented methods"""
    if getattr(rediscluster, "_datadog_patch", False):
        return
    setattr(rediscluster, "_datadog_patch", True)

    _w = wrapt.wrap_function_wrapper
    if REDISCLUSTER_VERSION >= (2, 0, 0):
        _w("rediscluster", "client.RedisCluster.execute_command", traced_execute_command(config.rediscluster))
        _w("rediscluster", "client.RedisCluster.pipeline", traced_pipeline)
        _w("rediscluster", "pipeline.ClusterPipeline.execute", traced_execute_pipeline)
        Pin().onto(rediscluster.RedisCluster)
    else:
        _w("rediscluster", "StrictRedisCluster.execute_command", traced_execute_command(config.rediscluster))
        _w("rediscluster", "StrictRedisCluster.pipeline", traced_pipeline)
        _w("rediscluster", "StrictClusterPipeline.execute", traced_execute_pipeline)
        Pin().onto(rediscluster.StrictRedisCluster)


def unpatch():
    if getattr(rediscluster, "_datadog_patch", False):
        setattr(rediscluster, "_datadog_patch", False)

        if REDISCLUSTER_VERSION >= (2, 0, 0):
            unwrap(rediscluster.client.RedisCluster, "execute_command")
            unwrap(rediscluster.client.RedisCluster, "pipeline")
            unwrap(rediscluster.pipeline.ClusterPipeline, "execute")
        else:
            unwrap(rediscluster.StrictRedisCluster, "execute_command")
            unwrap(rediscluster.StrictRedisCluster, "pipeline")
            unwrap(rediscluster.StrictClusterPipeline, "execute")


#
# tracing functions
#


def traced_execute_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    cmds = [stringify_cache_args(c.args) for c in instance.command_stack]
    resource = "\n".join(cmds)
    tracer = pin.tracer
    with tracer.trace(
        redisx.CMD,
        resource=resource,
        service=trace_utils.ext_service(pin, config.rediscluster, "rediscluster"),
        span_type=SpanTypes.REDIS,
    ) as s:
        s.set_tag(SPAN_MEASURED_KEY)
        s.set_tag(redisx.RAWCMD, resource)
        s.set_metric(redisx.PIPELINE_LEN, len(instance.command_stack))

        # set analytics sample rate if enabled
        s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.rediscluster.get_analytics_sample_rate())

        return func(*args, **kwargs)
