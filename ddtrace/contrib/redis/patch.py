
# 3p
import wrapt
import redis

# project
from ddtrace import Pin
from ddtrace.ext import redis as redisx
from .util import format_command_args, _extract_conn_tags


def patch():
    """ patch will patch the redis library to add tracing. """
    patch_target(redis.Redis)
    patch_target(redis.StrictRedis)

def patch_target(target, pin=None):
    if not pin:
        pin = Pin(service="redis", app="redis")

    pin.onto(target)

    # monkeypatch all of the methods.
    targets = [
        ('execute_command', _execute_command),
        ('pipeline', _pipeline),
    ]
    for method_name, wrapper in targets:
        method = getattr(target, method_name, None)
        if method is None:
            continue
        setattr(target, method_name, wrapt.FunctionWrapper(method, wrapper))
    return target

#
# tracing functions
#

def _execute_command(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    service = pin.service
    tracer = pin.tracer

    with tracer.trace('redis.command', service=service, span_type='redis') as s:
        query = format_command_args(args)
        s.resource = query
        s.set_tag(redisx.RAWCMD, query)
        s.set_tags(_get_tags(instance))
        s.set_metric(redisx.ARGS_LEN, len(args))
        # run the command
        return func(*args, **kwargs)

def _pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)
    # create the pipeline and monkeypatch it
    pipeline = func(*args, **kwargs)
    pin.onto(pipeline)
    setattr(
        pipeline,
        'execute', wrapt.FunctionWrapper(pipeline.execute, _execute_pipeline))
    setattr(
        pipeline,
        'immediate_execute_command',
        wrapt.FunctionWrapper(pipeline.immediate_execute_command, _execute_command))
    return pipeline

def _execute_pipeline(func, instance, args, kwargs):
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
