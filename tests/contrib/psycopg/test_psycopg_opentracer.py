# 3p
import psycopg2
import opentracing

# project
import ddtrace
from ddtrace.contrib.psycopg.patch import patch, unpatch

# testing
from tests.contrib.config import POSTGRES_CONFIG
from tests.opentracer.utils import opentracer_init


class TestPsycopgPatchOpentracer(object):
    """Test using both the Datadog and opentracer tracers."""

    def setUp(self):
        self._dd_tracer = ddtrace.tracer
        self.ot_tracer, self.dd_tracer = opentracer_init()
        self.writer = self.dd_tracer.writer

    def tearDown(self):
        unpatch()

        ddtrace.tracer = self._dd_tracer

    def test_simple_trace(self):
        patch()

        conn = psycopg2.connect(**POSTGRES_CONFIG)

        def my_ot_traced_func():
            with opentracing.tracer.start_span("my_ot_span"):
                conn.cursor().execute("select 'blah'")

        my_ot_traced_func()
        spans = self.writer.pop()

        assert len(spans) == 2
        assert spans[0].name == "my_ot_span"
        assert spans[0].parent_id is None
        assert spans[1].name == "postgres.query"
        assert spans[1].parent_id is spans[0].span_id

    def test_wrapped_trace(self):
        patch()

        conn = psycopg2.connect(**POSTGRES_CONFIG)

        @ddtrace.tracer.wrap("my_ot_traced_func")
        def my_ot_traced_func():
            with opentracing.tracer.start_span("my_ot_span"):
                conn.cursor().execute("select 'blah'")

        my_ot_traced_func()
        spans = self.writer.pop()

        assert len(spans) == 3
        assert spans[0].name == "my_ot_traced_func"
        assert spans[0].parent_id is None
        assert spans[1].name == "my_ot_span"
        assert spans[1].parent_id is spans[0].span_id
        assert spans[2].name == "postgres.query"
        assert spans[2].parent_id is spans[1].span_id
