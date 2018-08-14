# 3p
import rediscluster
import wrapt

# project
from ...pin import Pin
from ...ext import AppTypes, redis as redisx
from ...utils.wrappers import unwrap
from ..redis.patch import traced_execute_command, traced_pipeline
from ..redis.util import format_command_args


def patch():
    """Patch the instrumented methods
    """
    if getattr(rediscluster, '_datadog_patch', False):
        return
    setattr(rediscluster, '_datadog_patch', True)

    _w = wrapt.wrap_function_wrapper
    _w('rediscluster', 'StrictRedisCluster.execute_command', traced_execute_command)
    _w('rediscluster', 'StrictRedisCluster.pipeline', traced_pipeline)
    _w('rediscluster', 'StrictClusterPipeline.execute', traced_execute_pipeline)
    Pin(service=redisx.DEFAULT_SERVICE, app=redisx.APP, app_type=AppTypes.db).onto(rediscluster.StrictRedisCluster)


def unpatch():
    if getattr(rediscluster, '_datadog_patch', False):
        setattr(rediscluster, '_datadog_patch', False)
        unwrap(rediscluster.StrictRedisCluster, 'execute_command')
        unwrap(rediscluster.StrictRedisCluster, 'pipeline')
        unwrap(rediscluster.StrictClusterPipeline, 'execute')


#
# tracing functions
#

def traced_execute_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    cmds = [format_command_args(c.args) for c in instance.command_stack]
    resource = '\n'.join(cmds)
    tracer = pin.tracer
    with tracer.trace(redisx.CMD, resource=resource, service=pin.service) as s:
        s.span_type = redisx.TYPE
        s.set_tag(redisx.RAWCMD, resource)
        s.set_metric(redisx.PIPELINE_LEN, len(instance.command_stack))
        return func(*args, **kwargs)
