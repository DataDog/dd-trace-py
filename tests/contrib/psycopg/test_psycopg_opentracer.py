# 3p
import psycopg2
import opentracing

# project
import ddtrace
from ddtrace.contrib.psycopg.patch import patch, unpatch
from ddtrace import Pin

# testing
from tests.contrib.config import POSTGRES_CONFIG
from tests.opentracer.utils import opentracer_init


class TestPsycopgPatchOpentracer(object):
    """Test using both the Datadog and opentracer tracers."""

    def tearDown(self):
        unpatch()

    def test_simple_trace(self):
        ot_tracer, dd_tracer = opentracer_init()
        writer = dd_tracer.writer

        patch()

        conn = psycopg2.connect(**POSTGRES_CONFIG)

        def my_ot_traced_func():
            with opentracing.tracer.start_span("my_span"):
                conn.cursor().execute("select 'blah'")

        my_ot_traced_func()
        spans = writer.pop()

        assert len(spans) == 2
        assert spans[0].parent_id is None
        assert spans[1].parent_id is spans[0].span_id
