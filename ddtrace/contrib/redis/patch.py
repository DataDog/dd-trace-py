# 3p
import redis
import wrapt

# project
from ...pin import Pin
from ...ext import redis as redisx
from ...utils.wrappers import unwrap
from .util import format_command_args, _extract_conn_tags


def patch():
    """Patch the instrumented methods

    This duplicated doesn't look nice. The nicer alternative is to use an ObjectProxy on top
    of Redis and StrictRedis. However, it means that any "import redis.Redis" won't be instrumented.
    """
    if getattr(redis, '_datadog_patch', False):
        return
    setattr(redis, '_datadog_patch', True)

    _w = wrapt.wrap_function_wrapper
    _w('redis', 'StrictRedis.execute_command', traced_execute_command)
    _w('redis', 'StrictRedis.pipeline', traced_pipeline)
    _w('redis', 'Redis.pipeline', traced_pipeline)
    _w('redis.client', 'BasePipeline.execute', traced_execute_pipeline)
    _w('redis.client', 'BasePipeline.immediate_execute_command', traced_execute_command)
    Pin(service="redis", app="redis", app_type="db").onto(redis.StrictRedis)

def unpatch():
    if getattr(redis, '_datadog_patch', False):
        setattr(redis, '_datadog_patch', False)
        unwrap(redis.StrictRedis, 'execute_command')
        unwrap(redis.StrictRedis, 'pipeline')
        unwrap(redis.Redis, 'pipeline')
        unwrap(redis.client.BasePipeline, 'execute')
        unwrap(redis.client.BasePipeline, 'immediate_execute_command')

#
# tracing functions
#

def traced_execute_command(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    with pin.tracer.trace('redis.command', service=pin.service, span_type='redis') as s:
        query = format_command_args(args)
        s.resource = query
        s.set_tag(redisx.RAWCMD, query)
        if pin.tags:
            s.set_tags(pin.tags)
        s.set_tags(_get_tags(instance))
        s.set_metric(redisx.ARGS_LEN, len(args))
        # run the command
        return func(*args, **kwargs)

def traced_pipeline(func, instance, args, kwargs):
    pipeline = func(*args, **kwargs)
    pin = Pin.get_from(instance)
    if pin:
        pin.onto(pipeline)
    return pipeline

def traced_execute_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    # FIXME[matt] done in the agent. worth it?
    cmds = [format_command_args(c) for c, _ in instance.command_stack]
    resource = '\n'.join(cmds)
    tracer = pin.tracer
    with tracer.trace('redis.command', resource=resource, service=pin.service) as s:
        s.span_type = 'redis'
        s.set_tag(redisx.RAWCMD, resource)
        s.set_tags(_get_tags(instance))
        s.set_metric(redisx.PIPELINE_LEN, len(instance.command_stack))
        return func(*args, **kwargs)

def _get_tags(conn):
    return _extract_conn_tags(conn.connection_pool.connection_kwargs)
