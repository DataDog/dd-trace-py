import unittest

# 3p
from influxdb.client import InfluxDBClient
from influxdb.exceptions import InfluxDBClientError
from nose.tools import eq_, ok_

# project
from ddtrace import Pin, config
from ddtrace.ext import http, db
from ddtrace.contrib.influx.patch import patch, unpatch

# testing
from tests.opentracer.utils import init_tracer
from ..config import INFLUX_CONFIG
from ...test_tracer import get_dummy_tracer


TEST_DATABASE_NAME = 'ddtrace_test_database'
TEST_MEASUREMENT = 'ddtrace_measurement'

TEST_SERVICE = 'test'

TEST_HOST = INFLUX_CONFIG['host']
TEST_PORT = str(INFLUX_CONFIG['port'])


class TestInfluxDBPatch(unittest.TestCase):
    """
    InfluxDB integration test suite.
    Need a running InfluxDB database.
    Test cases with patching.
    """
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
        influxdb = InfluxDBClient(host=TEST_HOST, port=TEST_PORT)
        influxdb.create_database(TEST_DATABASE_NAME)
        patch()

    def tearDown(self):
        """Clean InfluxDB - drop the test database."""
        influxdb = InfluxDBClient(host=TEST_HOST, port=TEST_PORT)
        influxdb.drop_database(TEST_DATABASE_NAME)
        unpatch()

    def test_ping(self):
        """Simple response"""
        connection = InfluxDBClient(host=TEST_HOST, port=TEST_PORT)

        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        # Test ping database
        connection.ping()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, TEST_SERVICE)
        eq_(span.name, 'influx.request')
        eq_(span.span_type, 'sql')
        eq_(span.error, 0)
        eq_(span.get_tag(http.METHOD), 'GET')
        eq_(span.get_tag(http.URL), 'ping')

    def test_write_and_read(self):
        """Write points to server, read them back out."""

        connection = InfluxDBClient(host=TEST_HOST, port=TEST_PORT)

        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        connection.write_points(points=self.dummy_points, database=TEST_DATABASE_NAME, tags={'host': 'server01'})

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.error, 0)
        eq_(span.get_tag(http.METHOD), 'POST')
        eq_(span.get_tag(http.URL), 'write')

        # Search data
        result = connection.query('SELECT * from {}'.format(TEST_MEASUREMENT), database=TEST_DATABASE_NAME)

        assert result, result
        assert len(result) == 1, result

        query_points = list(result.get_points(TEST_MEASUREMENT))

        # Tags were applied on the way in.
        for p in query_points:
            assert p['host'] == 'server01'

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)  # query + http

        span = spans[0]

        eq_(span.resource, 'SELECT * from {}'.format(TEST_MEASUREMENT))
        eq_(span.get_tag(http.METHOD), 'GET')
        eq_(span.get_tag(http.URL), 'query')
        eq_(span.get_tag(db.NAME), TEST_DATABASE_NAME)

        # Delete the measurement
        connection.delete_series(TEST_DATABASE_NAME, TEST_MEASUREMENT)

    def test_unexpected_response_code(self):
        """Force server to respond with an HTTP/204, raising an InfluxDBClientError"""
        connection = InfluxDBClient(host=TEST_HOST, port=TEST_PORT)

        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        with self.assertRaises(InfluxDBClientError):
            connection.request(url='ping', expected_response_code=200)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        span = spans[0]

        # This span describes an error state
        eq_(span.error, 1)
        eq_(span.get_tag(http.STATUS_CODE), '204')
        eq_(span.resource, 'ping')

        # No response body from the server
        eq_('No error message returned from InfluxDB', span.get_tag('influx.error'))

        # Exception
        # There's no error body returned from InfluxDB for an HTTP 204, so this error.msg is not very useful.
        eq_(span.get_tag('error.msg'), '204: ')
        ok_('InfluxDBClientError' in span.get_tag('error.type'))
        ok_('InfluxDBClientError' in span.get_tag('error.stack'))
        ok_('Traceback (most recent call last)' in span.get_tag('error.stack'))

    def test_malformed_query_produces_error_body(self):
        """Force server to respond with an HTTP/400 for a malformed query, raising an InfluxDBClientError
        and populating error.msg"""
        connection = InfluxDBClient(host=TEST_HOST, port=TEST_PORT)

        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        query_text_string = 'SELECT * FROM Invalid Query Syntax WHERE "1" = "1"'
        with self.assertRaises(InfluxDBClientError):
            r = connection.query(query_text_string, database=TEST_DATABASE_NAME)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        span = spans[0]

        # This span describes an error state
        eq_(span.error, 1)
        eq_(span.get_tag(http.STATUS_CODE), '400')
        eq_(span.resource, query_text_string)  # The bad query is recorded unmodified here, the "1" parameters will be quantized by trace-agent

        # Response body from the server, parsed
        eq_('error parsing query: found QUERY, expected ; at line 1, char 23', span.get_tag('influx.error'))

        # Exception
        ok_('error parsing query' in span.get_tag('error.msg'))  # exception rendered to a string
        ok_('InfluxDBClientError' in span.get_tag('error.type'))
        ok_('InfluxDBClientError' in span.get_tag('error.stack'))
        ok_('Traceback (most recent call last)' in span.get_tag('error.stack'))

    def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        connection = InfluxDBClient(host=TEST_HOST, port=TEST_PORT)
        Pin(service=TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        # Fetch version
        connection.ping()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        # Test unpatch
        unpatch()

        connection = InfluxDBClient(host=TEST_HOST, port=TEST_PORT)

        # Fetch version; no spans generated.
        connection.ping()

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        connection = InfluxDBClient(host=TEST_HOST, port=TEST_PORT)
        Pin(service=TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        # Fetch version; span generated
        connection.ping()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

    def test_influxdb_opentracing(self):
        """Test nesting with OpenTracing spans."""
        tracer = get_dummy_tracer()
        writer = tracer.writer
        ot_tracer = init_tracer('my_svc', tracer)

        database = InfluxDBClient(host=TEST_HOST, port=TEST_PORT)
        Pin(service=TEST_SERVICE, tracer=tracer).onto(InfluxDBClient)

        # Write points to InfluxDB, inside the existing OpenTracing span.
        with ot_tracer.start_active_span('ot_span'):
            database.write_points(self.dummy_points, database=TEST_DATABASE_NAME)

        spans = writer.pop()
        assert spans

        eq_(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.service, 'my_svc')
        eq_(ot_span.resource, 'ot_span')

        eq_(dd_span.service, TEST_SERVICE)
        eq_(dd_span.name, 'influx.request')
        eq_(dd_span.span_type, 'sql')
        eq_(dd_span.error, 0)
        eq_(dd_span.get_tag(http.METHOD), 'POST')
        eq_(dd_span.get_tag(http.URL), 'write')
        eq_(dd_span.resource, 'write')


class InfluxConfigHonoredTestCase(unittest.TestCase):
    """Test config values for service_name and app_name are written to produced spans."""

    def setUp(self):
        # persist the original global config, to be restored in tearDown
        self._config = dict(config.influx)

        config.influx['service_name'] = 'tsdb'

        patch()
        self.tracer = get_dummy_tracer()
        self.connection = InfluxDBClient(host=TEST_HOST, port=TEST_PORT)
        # override pins to use our Dummy Tracer
        Pin.override(self.connection, tracer=self.tracer)

    def tearDown(self):
        # remove instrumentation from InfluxClient
        unpatch()
        self.connection = None
        # restore the global Config
        config.influx.update(self._config)
        self._config = None

    def test_config(self):
        """Simple response, with the global config"""

        # Test ping database
        self.connection.ping()

        spans = self.tracer.writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, 'tsdb')
        eq_(span.name, 'influx.request')
        eq_(span.resource, 'ping')
        eq_(span.span_type, 'sql')
        eq_(span.error, 0)
        eq_(span.get_tag(http.METHOD), 'GET')
        eq_(span.get_tag(http.URL), 'ping')
