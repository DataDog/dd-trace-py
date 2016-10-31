import logging

import wrapt
import redis

import ddtrace
from .util import format_command_args, _extract_conn_tags
from ...ext import redis as redisx

import redis


def patch():
    """ patch will patch the redis library to add tracing. """
    patch_target(redis.Redis)
    patch_target(redis.StrictRedis)

def patch_target(target, service=None, tracer=None):

    if isinstance(target, (redis.Redis, redis.StrictRedis)):
        if service: setattr(target, "datadog_service", service)
        if tracer: setattr(target, "datadog_tracer", tracer)

    targets = [
        ('execute_command', _execute_command)
    ]

    for method_name, wrapper in targets:
        method = getattr(target, method_name, None)
        if method is None:
            continue
        setattr(target, method_name, wrapt.FunctionWrapper(method, wrapper))

def _execute_command(func, instance, args, kwargs):
    service = getattr(instance, 'datadog_service', None) or 'redis'
    tracer = getattr(instance, 'datadog_tracer', None) or ddtrace.tracer
    with tracer.trace('redis.command', service=service, span_type='redis') as s:
        query = format_command_args(args)
        s.resource = query
        # non quantized version
        s.set_tag(redisx.RAWCMD, query)
        s.set_tags(_extract_conn_tags(instance.connection_pool.connection_kwargs))
        s.set_metric(redisx.ARGS_LEN, len(args))
        return func(*args, **kwargs)
