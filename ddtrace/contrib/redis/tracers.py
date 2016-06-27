"""
tracers exposed publicly
"""
# stdlib
import time

try:
    from redis import Redis
    from redis.client import StrictPipeline
except ImportError:
    Redis, StrictPipeline = object, object

# dogtrace
from .util import format_command_args, _extract_conn_tags
from ...ext import redis as redisx


DEFAULT_SERVICE = 'redis'


def get_traced_redis(ddtracer, service=DEFAULT_SERVICE):
    return _get_traced_redis(ddtracer, Redis, service)


def get_traced_redis_from(ddtracer, baseclass, service=DEFAULT_SERVICE):
    return _get_traced_redis(ddtracer, baseclass, service)

# pylint: disable=protected-access
def _get_traced_redis(ddtracer, baseclass, service):
    class TracedPipeline(StrictPipeline):
        _datadog_tracer = ddtracer
        _datadog_service = service

        def __init__(self, *args, **kwargs):
            self._datadog_pipeline_creation = time.time()
            super(TracedPipeline, self).__init__(*args, **kwargs)

        def execute(self, *args, **kwargs):
            commands, queries = [], []
            with self._datadog_tracer.trace('redis.pipeline') as s:
                s.service = self._datadog_service
                s.span_type = redisx.TYPE

                for cargs, _ in self.command_stack:
                    commands.append(cargs[0])
                    queries.append(format_command_args(cargs))

                s.set_tag(redisx.CMD, ', '.join(commands))
                query = '\n'.join(queries)
                s.resource = query

                s.set_tags(_extract_conn_tags(self.connection_pool.connection_kwargs))
                # FIXME[leo]: convert to metric?
                s.set_tag(redisx.PIPELINE_LEN, len(self.command_stack))
                s.set_tag(redisx.PIPELINE_AGE, time.time()-self._datadog_pipeline_creation)

                result = super(TracedPipeline, self).execute(self, *args, **kwargs)
                return result

        def immediate_execute_command(self, *args, **kwargs):
            command_name = args[0]

            with self._datadog_tracer.trace('redis.command') as s:
                s.service = self._datadog_service
                s.span_type = redisx.TYPE
                # currently no quantization on the client side
                s.resource = format_command_args(args)
                s.set_tag(redisx.CMD, (args or [None])[0])
                s.set_tags(_extract_conn_tags(self.connection_pool.connection_kwargs))
                # FIXME[leo]: convert to metric?
                s.set_tag(redisx.ARGS_LEN, len(args))

                s.set_tag(redisx.IMMEDIATE_PIPELINE, True)

                result = super(TracedPipeline, self).immediate_execute_command(*args, **options)
                return result

    class TracedRedis(baseclass):
        _datadog_tracer = ddtracer
        _datadog_service = service

        def execute_command(self, *args, **options):
            command_name = args[0]

            with self._datadog_tracer.trace('redis.command') as s:
                s.service = self._datadog_service
                s.span_type = redisx.TYPE
                # currently no quantization on the client side
                s.resource = format_command_args(args)
                s.set_tag(redisx.CMD, (args or [None])[0])
                s.set_tags(_extract_conn_tags(self.connection_pool.connection_kwargs))
                # FIXME[leo]: convert to metric?
                s.set_tag(redisx.ARGS_LEN, len(args))

                result = super(TracedRedis, self).execute_command(*args, **options)
                return result

        def pipeline(self, transaction=True, shard_hint=None):
            return TracedPipeline(
                self.connection_pool,
                self.response_callbacks,
                transaction,
                shard_hint
            )

    return TracedRedis
