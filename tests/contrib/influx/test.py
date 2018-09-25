import unittest

# 3p
from influxdb.client import InfluxDBClient
from influxdb.exceptions import InfluxDBClientError
from nose.tools import eq_

# project
from ddtrace import Pin
from ddtrace.ext import http, db
from ddtrace.contrib.influx.patch import patch, unpatch

# testing
from tests.opentracer.utils import init_tracer
from ..config import INFLUX_CONFIG
from ...test_tracer import get_dummy_tracer


class InfluxDBPatchTest(unittest.TestCase):
    """
    InfluxDB integration test suite.
    Need a running InfluxDB database.
    Test cases with patching.
    """
    TEST_DATABASE_NAME = 'ddtrace_test_database'
    TEST_MEASUREMENT = 'ddtrace_measurement'

    TEST_SERVICE = 'test'
    TEST_HOST = INFLUX_CONFIG['host']
    TEST_PORT = str(INFLUX_CONFIG['port'])

    dummy_points = [
        {'measurement': TEST_MEASUREMENT, 'tags': {'unit': 'percent'},
         'time': '2009-11-10T23:00:00Z', 'fields': {'value': 12.34}},
        {'measurement': TEST_MEASUREMENT, 'tags': {'direction': 'in'},
         'time': '2009-11-10T23:00:00Z', 'fields': {'value': 123.00}},
        {'measurement': TEST_MEASUREMENT, 'tags': {'direction': 'out'},
         'time': '2009-11-10T23:00:00Z', 'fields': {'value': 12.00}}
    ]

    def setUp(self):
        """Prepare InfluxDB"""
        influxdb = InfluxDBClient(host=self.TEST_HOST, port=self.TEST_PORT)
        influxdb.create_database(self.TEST_DATABASE_NAME)
        patch()

    def tearDown(self):
        """Clean InfluxDB - drop the test database."""
        influxdb = InfluxDBClient(host=self.TEST_HOST, port=self.TEST_PORT)
        influxdb.drop_database(self.TEST_DATABASE_NAME)
        unpatch()

    def test_ping(self):
        """Simple response"""
        db = InfluxDBClient(host=self.TEST_HOST, port=self.TEST_PORT)

        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        # Test ping database
        db.ping()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, 'influx.request')
        eq_(span.span_type, 'sql')
        eq_(span.error, 0)
        eq_(span.get_tag(http.METHOD), 'GET')
        eq_(span.get_tag(http.URL), 'ping')

    def test_write_and_read(self):
        """Write points to server, read them back out."""

        connection = InfluxDBClient(host=self.TEST_HOST, port=self.TEST_PORT)

        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        connection.write_points(points=self.dummy_points, database=self.TEST_DATABASE_NAME, tags={'host': 'server01'})

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.error, 0)
        eq_(span.get_tag(http.METHOD), 'POST')
        eq_(span.get_tag(http.URL), 'write')

        # Search data
        result = connection.query('SELECT * from %s' % self.TEST_MEASUREMENT, database=self.TEST_DATABASE_NAME)

        assert result, result
        assert len(result) == 1, result

        query_points = list(result.get_points(self.TEST_MEASUREMENT))

        # Tags were applied on the way in.
        for p in query_points:
            assert p['host'] == 'server01'

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)  # query + http

        span = spans[0]

        eq_(span.resource, 'SELECT * from %s' % self.TEST_MEASUREMENT)
        eq_(span.get_tag(http.METHOD), 'GET')
        eq_(span.get_tag(http.URL), 'query')
        eq_(span.get_tag(db.NAME), self.TEST_DATABASE_NAME)

        # Delete the measurement
        connection.delete_series(self.TEST_DATABASE_NAME, self.TEST_MEASUREMENT)

    def test_unexpected_response_code(self):
        """Force server to respond with an HTTP/204, raising an InfluxDBClientError"""
        db = InfluxDBClient(host=self.TEST_HOST, port=self.TEST_PORT)

        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        with self.assertRaises(InfluxDBClientError):
            db.request(url='ping', expected_response_code=200)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        span = spans[0]

        eq_(span.get_tag(http.STATUS_CODE), '204')
        eq_(span.resource, 'ping')

    def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        db = InfluxDBClient(host=self.TEST_HOST, port=self.TEST_PORT)
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        # Fetch version
        db.ping()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        # Test unpatch
        unpatch()

        db = InfluxDBClient(host=self.TEST_HOST, port=self.TEST_PORT)

        # Fetch version; no spans generated.
        db.ping()

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        db = InfluxDBClient(host=self.TEST_HOST, port=self.TEST_PORT)
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        # Fetch version; span generated
        db.ping()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

    def test_influxdb_opentracing(self):
        """Test nesting with OpenTracing spans."""
        tracer = get_dummy_tracer()
        writer = tracer.writer
        ot_tracer = init_tracer('my_svc', tracer)

        database = InfluxDBClient(host=self.TEST_HOST, port=self.TEST_PORT)
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        # Write points to InfluxDB, inside the existing OpenTracing span.
        with ot_tracer.start_active_span('ot_span'):
            database.write_points(self.dummy_points, database=self.TEST_DATABASE_NAME)

        spans = writer.pop()
        assert spans

        eq_(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.service, 'my_svc')
        eq_(ot_span.resource, 'ot_span')

        eq_(dd_span.service, self.TEST_SERVICE)
        eq_(dd_span.name, 'influx.request')
        eq_(dd_span.span_type, 'sql')
        eq_(dd_span.error, 0)
        eq_(dd_span.get_tag(http.METHOD), 'POST')
        eq_(dd_span.get_tag(http.URL), 'write')
        eq_(dd_span.resource, 'write')
