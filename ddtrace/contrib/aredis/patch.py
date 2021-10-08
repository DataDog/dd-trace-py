import aredis

from ddtrace import config
from ddtrace.vendor import wrapt

from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import net
from ...ext import redis as redisx
from ...internal.compat import stringify
from ...pin import Pin
from ...utils.wrappers import unwrap


VALUE_PLACEHOLDER = "?"
VALUE_MAX_LEN = 100
VALUE_TOO_LONG_MARK = "..."
CMD_MAX_LEN = 1000


config._add("aredis", dict(_default_service="redis"))


def patch():
    """Patch the instrumented methods"""
    if getattr(aredis, "_datadog_patch", False):
        return
    setattr(aredis, "_datadog_patch", True)

    _w = wrapt.wrap_function_wrapper

    _w("aredis.client", "StrictRedis.execute_command", traced_execute_command)
    _w("aredis.client", "StrictRedis.pipeline", traced_pipeline)
    _w("aredis.pipeline", "StrictPipeline.execute", traced_execute_pipeline)
    _w("aredis.pipeline", "StrictPipeline.immediate_execute_command", traced_execute_command)
    Pin(service=None, app=redisx.APP).onto(aredis.StrictRedis)


def unpatch():
    if getattr(aredis, "_datadog_patch", False):
        setattr(aredis, "_datadog_patch", False)

        unwrap(aredis.client.StrictRedis, "execute_command")
        unwrap(aredis.client.StrictRedis, "pipeline")
        unwrap(aredis.pipeline.StrictPipeline, "execute")
        unwrap(aredis.pipeline.StrictPipeline, "immediate_execute_command")


#
# tracing functions
#
async def traced_execute_command(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    with pin.tracer.trace(
        redisx.CMD, service=trace_utils.ext_service(pin, config.aredis, pin), span_type=SpanTypes.REDIS
    ) as s:
        s.set_tag(SPAN_MEASURED_KEY)
        query = format_command_args(args)
        s.resource = query
        s.set_tag(redisx.RAWCMD, query)
        if pin.tags:
            s.set_tags(pin.tags)
        s.set_tags(_get_tags(instance))
        s.set_metric(redisx.ARGS_LEN, len(args))
        # set analytics sample rate if enabled
        s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.aredis.get_analytics_sample_rate())
        # run the command
        return await func(*args, **kwargs)


async def traced_pipeline(func, instance, args, kwargs):
    pipeline = await func(*args, **kwargs)
    pin = Pin.get_from(instance)
    if pin:
        pin.onto(pipeline)
    return pipeline


async def traced_execute_pipeline(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    # FIXME[matt] done in the agent. worth it?
    cmds = [format_command_args(c) for c, _ in instance.command_stack]
    resource = "\n".join(cmds)
    tracer = pin.tracer
    with tracer.trace(
        redisx.CMD,
        resource=resource,
        service=trace_utils.ext_service(pin, config.aredis),
        span_type=SpanTypes.REDIS,
    ) as s:
        s.set_tag(SPAN_MEASURED_KEY)
        s.set_tag(redisx.RAWCMD, resource)
        s.set_tags(_get_tags(instance))
        s.set_metric(redisx.PIPELINE_LEN, len(instance.command_stack))

        # set analytics sample rate if enabled
        s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.aredis.get_analytics_sample_rate())

        return await func(*args, **kwargs)


def _extract_conn_tags(conn_kwargs):
    """Transform aredis conn info into dogtrace metas"""
    try:
        return {
            net.TARGET_HOST: conn_kwargs["host"],
            net.TARGET_PORT: conn_kwargs["port"],
            redisx.DB: conn_kwargs["db"] or 0,
        }
    except Exception:
        return {}


def format_command_args(args):
    """Format a command by removing unwanted values

    Restrict what we keep from the values sent (with a SET, HGET, LPUSH, ...):
      - Skip binary content
      - Truncate
    """
    length = 0
    out = []
    for arg in args:
        try:
            cmd = stringify(arg)

            if len(cmd) > VALUE_MAX_LEN:
                cmd = cmd[:VALUE_MAX_LEN] + VALUE_TOO_LONG_MARK

            if length + len(cmd) > CMD_MAX_LEN:
                prefix = cmd[: CMD_MAX_LEN - length]
                out.append("%s%s" % (prefix, VALUE_TOO_LONG_MARK))
                break

            out.append(cmd)
            length += len(cmd)
        except Exception:
            out.append(VALUE_PLACEHOLDER)
            break

    return " ".join(out)


def _get_tags(conn):
    return _extract_conn_tags(conn.connection_pool.connection_kwargs)
