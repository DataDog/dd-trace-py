import datetime

# project
from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.ext import http
from ddtrace.contrib.elasticsearch import get_traced_transport
from ddtrace.contrib.elasticsearch.elasticsearch import elasticsearch
from ddtrace.contrib.elasticsearch.patch import patch, unpatch

# testing
from tests.opentracer.utils import init_tracer
from tests.tracer.test_tracer import get_dummy_tracer
from ..config import ELASTICSEARCH_CONFIG
from ... import TracerTestCase, assert_span_http_status_code


class ElasticsearchTest(TracerTestCase):
    """
    Elasticsearch integration test suite.
    Need a running ElasticSearch
    """

    ES_INDEX = "ddtrace_index"
    ES_TYPE = "ddtrace_type"

    TEST_SERVICE = "test"
    TEST_PORT = str(ELASTICSEARCH_CONFIG["port"])

    def setUp(self):
        """Prepare ES"""
        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG["port"])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def tearDown(self):
        """Clean ES"""
        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG["port"])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def test_elasticsearch(self):
        """Test the elasticsearch integration

        All in this for now. Will split it later.
        """
        tracer = get_dummy_tracer()
        writer = tracer.writer
        transport_class = get_traced_transport(
            datadog_tracer=tracer,
            datadog_service=self.TEST_SERVICE,
        )

        es = elasticsearch.Elasticsearch(transport_class=transport_class, port=ELASTICSEARCH_CONFIG["port"])

        # Test index creation
        mapping = {"mapping": {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}}
        es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

        spans = writer.pop()
        assert spans
        assert len(spans) == 1
        span = spans[0]

        TracerTestCase.assert_is_measured(span)
        assert span.service == self.TEST_SERVICE
        assert span.name == "elasticsearch.query"
        assert span.span_type == "elasticsearch"
        assert span.error == 0
        assert span.get_tag("elasticsearch.method") == "PUT"
        assert span.get_tag("elasticsearch.url") == "/%s" % self.ES_INDEX
        assert span.resource == "PUT /%s" % self.ES_INDEX

        # Put data
        args = {"index": self.ES_INDEX, "doc_type": self.ES_TYPE}
        es.index(id=10, body={"name": "ten", "created": datetime.date(2016, 1, 1)}, **args)
        es.index(id=11, body={"name": "eleven", "created": datetime.date(2016, 2, 1)}, **args)
        es.index(id=12, body={"name": "twelve", "created": datetime.date(2016, 3, 1)}, **args)

        spans = writer.pop()
        assert spans
        assert len(spans) == 3
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.error == 0
        assert span.get_tag("elasticsearch.method") == "PUT"
        assert span.get_tag("elasticsearch.url") == "/%s/%s/%s" % (self.ES_INDEX, self.ES_TYPE, 10)
        assert span.resource == "PUT /%s/%s/?" % (self.ES_INDEX, self.ES_TYPE)

        # Make the data available
        es.indices.refresh(index=self.ES_INDEX)

        spans = writer.pop()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.resource == "POST /%s/_refresh" % self.ES_INDEX
        assert span.get_tag("elasticsearch.method") == "POST"
        assert span.get_tag("elasticsearch.url") == "/%s/_refresh" % self.ES_INDEX

        # Search data
        result = es.search(sort=["name:desc"], size=100, body={"query": {"match_all": {}}}, **args)

        assert len(result["hits"]["hits"]) == 3, result

        spans = writer.pop()
        assert spans
        assert len(spans) == 1
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.resource == "GET /%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE)
        assert span.get_tag("elasticsearch.method") == "GET"
        assert span.get_tag("elasticsearch.url") == "/%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE)

        assert span.get_tag("elasticsearch.body").replace(" ", "") == '{"query":{"match_all":{}}}'
        assert set(span.get_tag("elasticsearch.params").split("&")) == {"sort=name%3Adesc", "size=100"}
        assert http.QUERY_STRING not in span.meta

        self.assertTrue(span.get_metric("elasticsearch.took") > 0)

        # Search by type not supported by default json encoder
        query = {"range": {"created": {"gte": datetime.date(2016, 2, 1)}}}
        result = es.search(size=100, body={"query": query}, **args)

        assert len(result["hits"]["hits"]) == 2, result

        # Raise error 404 with a non existent index
        writer.pop()
        try:
            es.get(index="non_existent_index", id=100, doc_type="_all")
            assert "error_not_raised" == "elasticsearch.exceptions.TransportError"
        except elasticsearch.exceptions.TransportError:
            spans = writer.pop()
            assert spans
            span = spans[0]
            TracerTestCase.assert_is_measured(span)
            assert_span_http_status_code(span, 404)

        # Raise error 400, the index 10 is created twice
        try:
            es.indices.create(index=10)
            es.indices.create(index=10)
            assert "error_not_raised" == "elasticsearch.exceptions.TransportError"
        except elasticsearch.exceptions.TransportError:
            spans = writer.pop()
            assert spans
            span = spans[-1]
            TracerTestCase.assert_is_measured(span)
            assert_span_http_status_code(span, 400)

        # Drop the index, checking it won't raise exception on success or failure
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def test_elasticsearch_ot(self):
        """Shortened OpenTracing version of test_elasticsearch."""
        tracer = get_dummy_tracer()
        writer = tracer.writer
        ot_tracer = init_tracer("my_svc", tracer)

        transport_class = get_traced_transport(
            datadog_tracer=tracer,
            datadog_service=self.TEST_SERVICE,
        )

        es = elasticsearch.Elasticsearch(transport_class=transport_class, port=ELASTICSEARCH_CONFIG["port"])

        # Test index creation
        mapping = {"mapping": {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}}

        with ot_tracer.start_active_span("ot_span"):
            es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

        spans = writer.pop()
        assert spans
        assert len(spans) == 2
        ot_span, dd_span = spans

        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert ot_span.service == "my_svc"
        assert ot_span.resource == "ot_span"

        TracerTestCase.assert_is_measured(dd_span)
        assert dd_span.service == self.TEST_SERVICE
        assert dd_span.name == "elasticsearch.query"
        assert dd_span.span_type == "elasticsearch"
        assert dd_span.error == 0
        assert dd_span.get_tag("elasticsearch.method") == "PUT"
        assert dd_span.get_tag("elasticsearch.url") == "/%s" % self.ES_INDEX
        assert dd_span.resource == "PUT /%s" % self.ES_INDEX


class ElasticsearchPatchTest(TracerTestCase):
    """
    Elasticsearch integration test suite.
    Need a running ElasticSearch.
    Test cases with patching.
    Will merge when patching will be the default/only way.
    """

    ES_INDEX = "ddtrace_index"
    ES_TYPE = "ddtrace_type"

    TEST_SERVICE = "test"
    TEST_PORT = str(ELASTICSEARCH_CONFIG["port"])

    def setUp(self):
        """Prepare ES"""
        super(ElasticsearchPatchTest, self).setUp()

        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG["port"])
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(es.transport)
        mapping = {"mapping": {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}}
        es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

        patch()

        self.es = es

    def tearDown(self):
        """Clean ES"""
        super(ElasticsearchPatchTest, self).tearDown()

        unpatch()
        self.es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def test_elasticsearch(self):
        es = self.es
        mapping = {"mapping": {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}}
        es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.service == self.TEST_SERVICE
        assert span.name == "elasticsearch.query"
        assert span.span_type == "elasticsearch"
        assert span.error == 0
        assert span.get_tag("elasticsearch.method") == "PUT"
        assert span.get_tag("elasticsearch.url") == "/%s" % self.ES_INDEX
        assert span.resource == "PUT /%s" % self.ES_INDEX

        args = {"index": self.ES_INDEX, "doc_type": self.ES_TYPE}
        es.index(id=10, body={"name": "ten", "created": datetime.date(2016, 1, 1)}, **args)
        es.index(id=11, body={"name": "eleven", "created": datetime.date(2016, 2, 1)}, **args)
        es.index(id=12, body={"name": "twelve", "created": datetime.date(2016, 3, 1)}, **args)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 3
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.error == 0
        assert span.get_tag("elasticsearch.method") == "PUT"
        assert span.get_tag("elasticsearch.url") == "/%s/%s/%s" % (self.ES_INDEX, self.ES_TYPE, 10)
        assert span.resource == "PUT /%s/%s/?" % (self.ES_INDEX, self.ES_TYPE)

        args = {"index": self.ES_INDEX, "doc_type": self.ES_TYPE}
        es.indices.refresh(index=self.ES_INDEX)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.resource == "POST /%s/_refresh" % self.ES_INDEX
        assert span.get_tag("elasticsearch.method") == "POST"
        assert span.get_tag("elasticsearch.url") == "/%s/_refresh" % self.ES_INDEX

        # search data
        args = {"index": self.ES_INDEX, "doc_type": self.ES_TYPE}
        with self.override_http_config("elasticsearch", dict(trace_query_string=True)):
            es.index(id=10, body={"name": "ten", "created": datetime.date(2016, 1, 1)}, **args)
            es.index(id=11, body={"name": "eleven", "created": datetime.date(2016, 2, 1)}, **args)
            es.index(id=12, body={"name": "twelve", "created": datetime.date(2016, 3, 1)}, **args)
            result = es.search(sort=["name:desc"], size=100, body={"query": {"match_all": {}}}, **args)

        assert len(result["hits"]["hits"]) == 3, result
        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 4
        span = spans[-1]
        TracerTestCase.assert_is_measured(span)
        assert span.resource == "GET /%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE)
        assert span.get_tag("elasticsearch.method") == "GET"
        assert span.get_tag("elasticsearch.url") == "/%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE)
        assert span.get_tag("elasticsearch.body").replace(" ", "") == '{"query":{"match_all":{}}}'
        assert set(span.get_tag("elasticsearch.params").split("&")) == {"sort=name%3Adesc", "size=100"}
        assert set(span.get_tag(http.QUERY_STRING).split("&")) == {"sort=name%3Adesc", "size=100"}

        self.assertTrue(span.get_metric("elasticsearch.took") > 0)

        # Search by type not supported by default json encoder
        query = {"range": {"created": {"gte": datetime.date(2016, 2, 1)}}}
        result = es.search(size=100, body={"query": query}, **args)

        assert len(result["hits"]["hits"]) == 2, result

    def test_analytics_default(self):
        es = self.es
        mapping = {"mapping": {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}}
        es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertIsNone(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_with_rate(self):
        with self.override_config("elasticsearch", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            es = self.es
            mapping = {"mapping": {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}}
            es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

            spans = self.get_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_without_rate(self):
        with self.override_config("elasticsearch", dict(analytics_enabled=True)):
            es = self.es
            mapping = {"mapping": {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}}
            es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

            spans = self.get_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)

    def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG["port"])
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(es.transport)

        # Test index creation
        es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        self.reset()
        unpatch()

        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG["port"])

        # Test index creation
        es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = self.get_spans()
        self.reset()
        assert not spans, spans

        # Test patch again
        self.reset()
        patch()

        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG["port"])
        Pin(service=self.TEST_SERVICE, tracer=self.tracer).onto(es.transport)

        # Test index creation
        es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service(self):
        """
        When a user specifies a service for the app
            The elasticsearch integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        self.es.indices.create(index=self.ES_INDEX, ignore=400)
        Pin(service="es", tracer=self.tracer).onto(self.es.transport)
        spans = self.get_spans()
        self.reset()
        assert len(spans) == 1
        assert spans[0].service != "mysvc"

    def test_none_param(self):
        try:
            self.es.transport.perform_request("GET", "/test-index", body="{}", params=None)
        except elasticsearch.exceptions.NotFoundError:
            pass
        spans = self.get_spans()
        assert len(spans) == 1
