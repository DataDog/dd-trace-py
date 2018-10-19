import datetime
import unittest

# 3p
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import TransportError
from nose.tools import eq_

# project
from ddtrace import Pin
from ddtrace.ext import http
from ddtrace.contrib.elasticsearch import get_traced_transport
from ddtrace.contrib.elasticsearch.patch import patch, unpatch

# testing
from tests.opentracer.utils import init_tracer
from ..config import ELASTICSEARCH_CONFIG
from ...test_tracer import get_dummy_tracer


class ElasticsearchTest(unittest.TestCase):
    """
    Elasticsearch integration test suite.
    Need a running ElasticSearch
    """
    ES_INDEX = 'ddtrace_index'
    ES_TYPE = 'ddtrace_type'

    TEST_SERVICE = 'test'
    TEST_PORT = str(ELASTICSEARCH_CONFIG['port'])

    def setUp(self):
        """Prepare ES"""
        es = Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def tearDown(self):
        """Clean ES"""
        es = Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def test_elasticsearch(self):
        """Test the elasticsearch integration

        All in this for now. Will split it later.
        """
        tracer = get_dummy_tracer()
        writer = tracer.writer
        transport_class = get_traced_transport(
                datadog_tracer=tracer,
                datadog_service=self.TEST_SERVICE)

        es = Elasticsearch(transport_class=transport_class, port=ELASTICSEARCH_CONFIG['port'])

        # Test index creation
        mapping = {"mapping": {"properties": {"created": {"type":"date", "format": "yyyy-MM-dd"}}}}
        es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, "elasticsearch.query")
        eq_(span.span_type, "elasticsearch")
        eq_(span.error, 0)
        eq_(span.get_tag('elasticsearch.method'), "PUT")
        eq_(span.get_tag('elasticsearch.url'), "/%s" % self.ES_INDEX)
        eq_(span.resource, "PUT /%s" % self.ES_INDEX)

        # Put data
        args = {'index':self.ES_INDEX, 'doc_type':self.ES_TYPE}
        es.index(id=10, body={'name': 'ten', 'created': datetime.date(2016, 1, 1)}, **args)
        es.index(id=11, body={'name': 'eleven', 'created': datetime.date(2016, 2, 1)}, **args)
        es.index(id=12, body={'name': 'twelve', 'created': datetime.date(2016, 3, 1)}, **args)

        spans = writer.pop()
        assert spans
        eq_(len(spans), 3)
        span = spans[0]
        eq_(span.error, 0)
        eq_(span.get_tag('elasticsearch.method'), "PUT")
        eq_(span.get_tag('elasticsearch.url'), "/%s/%s/%s" % (self.ES_INDEX, self.ES_TYPE, 10))
        eq_(span.resource, "PUT /%s/%s/?" % (self.ES_INDEX, self.ES_TYPE))

        # Make the data available
        es.indices.refresh(index=self.ES_INDEX)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.resource, "POST /%s/_refresh" % self.ES_INDEX)
        eq_(span.get_tag('elasticsearch.method'), "POST")
        eq_(span.get_tag('elasticsearch.url'), "/%s/_refresh" % self.ES_INDEX)

        # Search data
        result = es.search(sort=['name:desc'], size=100,
                body={"query":{"match_all":{}}}, **args)

        assert len(result["hits"]["hits"]) == 3, result

        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.resource,
                "GET /%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE))
        eq_(span.get_tag('elasticsearch.method'), "GET")
        eq_(span.get_tag('elasticsearch.url'),
                "/%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE))
        eq_(span.get_tag('elasticsearch.body').replace(" ", ""), '{"query":{"match_all":{}}}')
        eq_(set(span.get_tag('elasticsearch.params').split('&')), {'sort=name%3Adesc', 'size=100'})

        self.assertTrue(span.get_metric('elasticsearch.took') > 0)

        # Search by type not supported by default json encoder
        query = {"range": {"created": {"gte": datetime.date(2016, 2, 1)}}}
        result = es.search(size=100, body={"query": query}, **args)

        assert len(result["hits"]["hits"]) == 2, result

        # Raise error 404 with a non existent index
        writer.pop()
        try:
            es.get(index="non_existent_index", id=100, doc_type="_all")
            eq_("error_not_raised", "TransportError")
        except TransportError as e:
            spans = writer.pop()
            assert spans
            span = spans[0]
            eq_(span.get_tag(http.STATUS_CODE), u'404')

        # Raise error 400, the index 10 is created twice
        try:
            es.indices.create(index=10)
            es.indices.create(index=10)
            eq_("error_not_raised", "TransportError")
        except TransportError as e:
            spans = writer.pop()
            assert spans
            span = spans[-1]
            eq_(span.get_tag(http.STATUS_CODE), u'400')

        # Drop the index, checking it won't raise exception on success or failure
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def test_elasticsearch_ot(self):
        """Shortened OpenTracing version of test_elasticsearch."""
        tracer = get_dummy_tracer()
        writer = tracer.writer
        ot_tracer = init_tracer('my_svc', tracer)

        transport_class = get_traced_transport(
                datadog_tracer=tracer,
                datadog_service=self.TEST_SERVICE)

        es = Elasticsearch(transport_class=transport_class, port=ELASTICSEARCH_CONFIG['port'])

        # Test index creation
        mapping = {"mapping": {"properties": {"created": {"type":"date", "format": "yyyy-MM-dd"}}}}

        with ot_tracer.start_active_span('ot_span'):
            es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

        spans = writer.pop()
        assert spans
        eq_(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.service, "my_svc")
        eq_(ot_span.resource, "ot_span")

        eq_(dd_span.service, self.TEST_SERVICE)
        eq_(dd_span.name, "elasticsearch.query")
        eq_(dd_span.span_type, "elasticsearch")
        eq_(dd_span.error, 0)
        eq_(dd_span.get_tag('elasticsearch.method'), "PUT")
        eq_(dd_span.get_tag('elasticsearch.url'), "/%s" % self.ES_INDEX)
        eq_(dd_span.resource, "PUT /%s" % self.ES_INDEX)


class ElasticsearchPatchTest(unittest.TestCase):
    """
    Elasticsearch integration test suite.
    Need a running ElasticSearch.
    Test cases with patching.
    Will merge when patching will be the default/only way.
    """
    ES_INDEX = 'ddtrace_index'
    ES_TYPE = 'ddtrace_type'

    TEST_SERVICE = 'test'
    TEST_PORT = str(ELASTICSEARCH_CONFIG['port'])

    def setUp(self):
        """Prepare ES"""
        es = Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])
        patch()

    def tearDown(self):
        """Clean ES"""
        unpatch()
        es = Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def test_elasticsearch(self):
        """Test the elasticsearch integration

        All in this for now. Will split it later.
        """
        """Test the elasticsearch integration with patching

        """
        es = Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])

        tracer = get_dummy_tracer()
        writer = tracer.writer
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(es.transport)


        # Test index creation
        mapping = {"mapping": {"properties": {"created": {"type":"date", "format": "yyyy-MM-dd"}}}}
        es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, "elasticsearch.query")
        eq_(span.span_type, "elasticsearch")
        eq_(span.error, 0)
        eq_(span.get_tag('elasticsearch.method'), "PUT")
        eq_(span.get_tag('elasticsearch.url'), "/%s" % self.ES_INDEX)
        eq_(span.resource, "PUT /%s" % self.ES_INDEX)

        # Put data
        args = {'index':self.ES_INDEX, 'doc_type':self.ES_TYPE}
        es.index(id=10, body={'name': 'ten', 'created': datetime.date(2016, 1, 1)}, **args)
        es.index(id=11, body={'name': 'eleven', 'created': datetime.date(2016, 2, 1)}, **args)
        es.index(id=12, body={'name': 'twelve', 'created': datetime.date(2016, 3, 1)}, **args)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 3)
        span = spans[0]
        eq_(span.error, 0)
        eq_(span.get_tag('elasticsearch.method'), "PUT")
        eq_(span.get_tag('elasticsearch.url'), "/%s/%s/%s" % (self.ES_INDEX, self.ES_TYPE, 10))
        eq_(span.resource, "PUT /%s/%s/?" % (self.ES_INDEX, self.ES_TYPE))

        # Make the data available
        es.indices.refresh(index=self.ES_INDEX)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.resource, "POST /%s/_refresh" % self.ES_INDEX)
        eq_(span.get_tag('elasticsearch.method'), "POST")
        eq_(span.get_tag('elasticsearch.url'), "/%s/_refresh" % self.ES_INDEX)

        # Search data
        result = es.search(sort=['name:desc'], size=100,
                           body={"query":{"match_all":{}}}, **args)

        assert len(result["hits"]["hits"]) == 3, result

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.resource,
            "GET /%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE))
        eq_(span.get_tag('elasticsearch.method'), "GET")
        eq_(span.get_tag('elasticsearch.url'),
            "/%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE))
        eq_(span.get_tag('elasticsearch.body').replace(" ", ""), '{"query":{"match_all":{}}}')
        eq_(set(span.get_tag('elasticsearch.params').split('&')), {'sort=name%3Adesc', 'size=100'})

        self.assertTrue(span.get_metric('elasticsearch.took') > 0)

        # Search by type not supported by default json encoder
        query = {"range": {"created": {"gte": datetime.date(2016, 2, 1)}}}
        result = es.search(size=100, body={"query": query}, **args)

        assert len(result["hits"]["hits"]) == 2, result

        # Drop the index, checking it won't raise exception on success or failure
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        es = Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(es.transport)

        # Test index creation
        es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        # Test unpatch
        unpatch()

        es = Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])

        # Test index creation
        es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        es = Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(es.transport)

        # Test index creation
        es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
