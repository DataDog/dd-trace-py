"""
tracers exposed publicly
"""
# stdlib
import time

from redis import StrictRedis
from redis.client import StrictPipeline

# dogtrace
from .util import format_command_args, _extract_conn_tags
from ...ext import redis as redisx


DEFAULT_SERVICE = 'redis'


def get_traced_redis(ddtracer, service=DEFAULT_SERVICE, meta=None):
    return _get_traced_redis(ddtracer, StrictRedis, service, meta)


def get_traced_redis_from(ddtracer, baseclass, service=DEFAULT_SERVICE, meta=None):
    return _get_traced_redis(ddtracer, baseclass, service, meta)

# pylint: disable=protected-access
def _get_traced_redis(ddtracer, baseclass, service, meta):
    basepipeline = StrictPipeline
    try:
        basepipeline = baseclass().pipeline().__class__
    except:
        pass

    class TracedPipeline(basepipeline):
        _datadog_tracer = ddtracer
        _datadog_service = service
        _datadog_meta = meta

        def __init__(self, *args, **kwargs):
            self._datadog_pipeline_creation = time.time()
            super(TracedPipeline, self).__init__(*args, **kwargs)

        def execute(self, *args, **kwargs):
            queries = []
            with self._datadog_tracer.trace('redis.pipeline') as s:
                if s.sampled:
                    s.service = self._datadog_service
                    s.span_type = redisx.TYPE

                    for cargs, _ in self.command_stack:
                        queries.append(format_command_args(cargs))

                    query = '\n'.join(queries)
                    s.resource = query
                    # non quantized version
                    s.set_tag(redisx.RAWCMD, query)

                    s.set_tags(_extract_conn_tags(self.connection_pool.connection_kwargs))
                    s.set_tags(self._datadog_meta)
                    # FIXME[leo]: convert to metric?
                    s.set_tag(redisx.PIPELINE_LEN, len(self.command_stack))
                    s.set_tag(redisx.PIPELINE_AGE, time.time()-self._datadog_pipeline_creation)

                return super(TracedPipeline, self).execute(self, *args, **kwargs)

        def immediate_execute_command(self, *args, **kwargs):
            command_name = args[0]

            with self._datadog_tracer.trace('redis.command') as s:
                if s.sampled:
                    s.service = self._datadog_service
                    s.span_type = redisx.TYPE

                    query = format_command_args(args)
                    s.resource = query
                    # non quantized version
                    s.set_tag(redisx.RAWCMD, query)

                    s.set_tags(_extract_conn_tags(self.connection_pool.connection_kwargs))
                    s.set_tags(self._datadog_meta)
                    # FIXME[leo]: convert to metric?
                    s.set_tag(redisx.ARGS_LEN, len(args))

                    s.set_tag(redisx.IMMEDIATE_PIPELINE, True)

                return super(TracedPipeline, self).immediate_execute_command(*args, **options)

    class TracedRedis(baseclass):
        _datadog_tracer = ddtracer
        _datadog_service = service
        _datadog_meta = meta

        @classmethod
        def set_datadog_meta(cls, meta):
            cls._datadog_meta = meta

        def execute_command(self, *args, **options):
            with self._datadog_tracer.trace('redis.command') as s:
                if s.sampled:
                    s.service = self._datadog_service
                    s.span_type = redisx.TYPE

                    query = format_command_args(args)
                    s.resource = query
                    # non quantized version
                    s.set_tag(redisx.RAWCMD, query)

                    s.set_tags(_extract_conn_tags(self.connection_pool.connection_kwargs))
                    s.set_tags(self._datadog_meta)
                    # FIXME[leo]: convert to metric?
                    s.set_tag(redisx.ARGS_LEN, len(args))

                return super(TracedRedis, self).execute_command(*args, **options)

        def pipeline(self, transaction=True, shard_hint=None):
            tp = TracedPipeline(
                self.connection_pool,
                self.response_callbacks,
                transaction,
                shard_hint
            )
            tp._datadog_meta = meta
            return tp

    return TracedRedis
