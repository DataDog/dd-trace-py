import confluent_kafka

from ddtrace.vendor import wrapt

from ...pin import Pin
from ..trace_utils import unwrap


def patch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        return
    setattr(confluent_kafka, "_datadog_patch", True)

    _w = wrapt.wrap_function_wrapper

    _w("redis", "Redis.execute_command", traced_execute_command(config.redis))
    _w("redis", "Redis.pipeline", traced_pipeline)
    Pin(service=None).onto(redis.StrictRedis)


def unpatch():
    if getattr(confluent_kafka, "_datadog_patch", False):
        setattr(confluent_kafka, "_datadog_patch", False)

    unwrap(redis.Redis, "execute_command")
    unwrap(redis.Redis, "pipeline")


#
# tracing functions
#
def traced_execute_command(integration_config):
    def _traced_execute_command(func, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        with _trace_redis_cmd(pin, integration_config, instance, args):
            return func(*args, **kwargs)

    return _traced_execute_command


def traced_pipeline(func, instance, args, kwargs):
    pipeline = func(*args, **kwargs)
    pin = Pin.get_from(instance)
    if pin:
        pin.onto(pipeline)
    return pipeline


def traced_execute_pipeline(integration_config):
    def _traced_execute_pipeline(func, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        cmds = [stringify_cache_args(c) for c, _ in instance.command_stack]
        resource = "\n".join(cmds)
        with _trace_redis_execute_pipeline(pin, integration_config, resource, instance):
            return func(*args, **kwargs)

    return _traced_execute_pipeline
