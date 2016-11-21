
# 3p
import wrapt
import redis

# project
from ddtrace import Pin
from ddtrace.ext import redis as redisx
from .util import format_command_args, _extract_conn_tags


# Original Redis methods
_Redis_execute_command = redis.Redis.execute_command
_Redis_pipeline = redis.Redis.pipeline
_StrictRedis_execute_command = redis.StrictRedis.execute_command
_StrictRedis_pipeline = redis.StrictRedis.pipeline

def patch():
    """Patch the instrumented methods

    This duplicated doesn't look nice. The nicer alternative is to use an ObjectProxy on top
    of Redis and StrictRedis. However, it means that any "import redis.Redis" won't be instrumented.
    """
    setattr(redis.Redis, 'execute_command',
            wrapt.FunctionWrapper(_Redis_execute_command, traced_execute_command))
    setattr(redis.StrictRedis, 'execute_command',
            wrapt.FunctionWrapper(_StrictRedis_execute_command, traced_execute_command))
    setattr(redis.Redis, 'pipeline',
            wrapt.FunctionWrapper(_Redis_pipeline, traced_pipeline))
    setattr(redis.StrictRedis, 'pipeline',
            wrapt.FunctionWrapper(_StrictRedis_pipeline, traced_pipeline))

    Pin(service="redis", app="redis", app_type="db").onto(redis.Redis)
    Pin(service="redis", app="redis", app_type="db").onto(redis.StrictRedis)

def unpatch():
    redis.Redis.execute_command = _Redis_execute_command
    redis.Redis.pipeline = _Redis_pipeline
    redis.StrictRedis.execute_command = _StrictRedis_execute_command
    redis.StrictRedis.pipeline = _StrictRedis_pipeline

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
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)
    # create the pipeline and patch it
    pipeline = func(*args, **kwargs)
    pin.onto(pipeline)
    if not isinstance(pipeline.execute, wrapt.FunctionWrapper):
        setattr(pipeline, 'execute',
                wrapt.FunctionWrapper(pipeline.execute, traced_execute_pipeline))
    if not isinstance(pipeline.immediate_execute_command, wrapt.FunctionWrapper):
        setattr(pipeline, 'immediate_execute_command',
                wrapt.FunctionWrapper(pipeline.immediate_execute_command, traced_execute_command))
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
