"""Instrument the redis module to report Redis queries.

``patch_all`` will automatically patch your Redis client to make it work.
::

    from ddtrace import Pin, patch
    import redis

    # If not patched yet, you can patch redis specifically
    patch(redis=True)

    # This will report a span with the default settings
    client = redis.StrictRedis(host="localhost", port=6379)
    client.get("my-key")

    # Use a pin to specify metadata related to this client
    Pin.override(client, service='redis-queue')
"""
from wrapt import wrap_function_wrapper as _w

from ...ext import AppTypes, redis as redisx
from ...pin import Pin
from ...utils.wrappers import unwrap
from ...utils.install import install_module_import_hook
from .tracers import get_traced_redis
from .util import format_command_args, _extract_conn_tags


__all__ = [
    'get_traced_redis',
    'patch',
]


def _patch_redis_client(redisclient):
    _w('redis.client', 'BasePipeline.execute', traced_execute_pipeline)
    _w('redis.client', 'BasePipeline.immediate_execute_command', traced_execute_command)


def _patch_redis(redis):
    _w('redis', 'StrictRedis.execute_command', traced_execute_command)
    _w('redis', 'StrictRedis.pipeline', traced_pipeline)
    _w('redis', 'Redis.pipeline', traced_pipeline)
    Pin(service=redisx.DEFAULT_SERVICE, app=redisx.APP, app_type=AppTypes.db).onto(redis.StrictRedis)


def patch():
    install_module_import_hook('redis', _patch_redis)
    install_module_import_hook('redis.client', _patch_redis_client)


def unpatch():
    import redis
    if getattr(redis, '_datadog_patch', False):
        return
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

    with pin.tracer.trace(redisx.CMD, service=pin.service, span_type=redisx.TYPE) as s:
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
    with tracer.trace(redisx.CMD, resource=resource, service=pin.service) as s:
        s.span_type = redisx.TYPE
        s.set_tag(redisx.RAWCMD, resource)
        s.set_tags(_get_tags(instance))
        s.set_metric(redisx.PIPELINE_LEN, len(instance.command_stack))
        return func(*args, **kwargs)

def _get_tags(conn):
    return _extract_conn_tags(conn.connection_pool.connection_kwargs)
