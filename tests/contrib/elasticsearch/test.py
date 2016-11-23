import unittest

# 3p
import elasticsearch
from nose.tools import eq_

# project
from ddtrace import Tracer, Pin
from ddtrace.contrib.elasticsearch import get_traced_transport, metadata
from ddtrace.contrib.elasticsearch.patch import patch, unpatch

# testing
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
        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def tearDown(self):
        """Clean ES"""
        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
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

        es = elasticsearch.Elasticsearch(transport_class=transport_class, port=ELASTICSEARCH_CONFIG['port'])

        # Test index creation
        es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, "elasticsearch.query")
        eq_(span.span_type, "elasticsearch")
        eq_(span.error, 0)
        eq_(span.get_tag(metadata.METHOD), "PUT")
        eq_(span.get_tag(metadata.URL), "/%s" % self.ES_INDEX)
        eq_(span.resource, "PUT /%s" % self.ES_INDEX)

        # Put data
        args = {'index':self.ES_INDEX, 'doc_type':self.ES_TYPE}
        es.index(id=10, body={'name': 'ten'}, **args)
        es.index(id=11, body={'name': 'eleven'}, **args)
        es.index(id=12, body={'name': 'twelve'}, **args)

        spans = writer.pop()
        assert spans
        eq_(len(spans), 3)
        span = spans[0]
        eq_(span.error, 0)
        eq_(span.get_tag(metadata.METHOD), "PUT")
        eq_(span.get_tag(metadata.URL), "/%s/%s/%s" % (self.ES_INDEX, self.ES_TYPE, 10))
        eq_(span.resource, "PUT /%s/%s/?" % (self.ES_INDEX, self.ES_TYPE))

        # Search data
        es.search(sort=['name:desc'], size=100,
                body={"query":{"match_all":{}}}, **args)

        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.resource,
                "GET /%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE))
        eq_(span.get_tag(metadata.METHOD), "GET")
        eq_(span.get_tag(metadata.URL),
                "/%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE))
        eq_(span.get_tag(metadata.BODY).replace(" ", ""), '{"query":{"match_all":{}}}')
        eq_(set(span.get_tag(metadata.PARAMS).split('&')), {'sort=name%3Adesc', 'size=100'})

        self.assertTrue(span.get_metric(metadata.TOOK) > 0)

        # Drop the index, checking it won't raise exception on success or failure
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])


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
        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])
        patch()

    def tearDown(self):
        """Clean ES"""
        unpatch()
        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def test_elasticsearch(self):
        """Test the elasticsearch integration

        All in this for now. Will split it later.
        """
        """Test the elasticsearch integration with patching

        """
        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])

        tracer = get_dummy_tracer()
        writer = tracer.writer
        pin = Pin(service=self.TEST_SERVICE, tracer=tracer)
        pin.onto(es)

        # Test index creation
        es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self.TEST_SERVICE)
        eq_(span.name, "elasticsearch.query")
        eq_(span.span_type, "elasticsearch")
        eq_(span.error, 0)
        eq_(span.get_tag(metadata.METHOD), "PUT")
        eq_(span.get_tag(metadata.URL), "/%s" % self.ES_INDEX)
        eq_(span.resource, "PUT /%s" % self.ES_INDEX)

        # Put data
        args = {'index':self.ES_INDEX, 'doc_type':self.ES_TYPE}
        es.index(id=10, body={'name': 'ten'}, **args)
        es.index(id=11, body={'name': 'eleven'}, **args)
        es.index(id=12, body={'name': 'twelve'}, **args)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 3)
        span = spans[0]
        eq_(span.error, 0)
        eq_(span.get_tag(metadata.METHOD), "PUT")
        eq_(span.get_tag(metadata.URL), "/%s/%s/%s" % (self.ES_INDEX, self.ES_TYPE, 10))
        eq_(span.resource, "PUT /%s/%s/?" % (self.ES_INDEX, self.ES_TYPE))

        # Make the data available
        es.indices.refresh(index=self.ES_INDEX)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.resource, "POST /%s/_refresh" % self.ES_INDEX)
        eq_(span.get_tag(metadata.METHOD), "POST")
        eq_(span.get_tag(metadata.URL), "/%s/_refresh" % self.ES_INDEX)

        # Search data
        result = es.search(sort=['name:desc'], size=100,
                           body={"query":{"match_all":{}}}, **args)

        assert len(result["hits"]) == 3, result

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.resource,
            "GET /%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE))
        eq_(span.get_tag(metadata.METHOD), "GET")
        eq_(span.get_tag(metadata.URL),
            "/%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE))
        eq_(span.get_tag(metadata.BODY).replace(" ", ""), '{"query":{"match_all":{}}}')
        eq_(set(span.get_tag(metadata.PARAMS).split('&')), {'sort=name%3Adesc', 'size=100'})

        self.assertTrue(span.get_metric(metadata.TOOK) > 0)

        # Drop the index, checking it won't raise exception on success or failure
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(es)

        # Test index creation
        es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        # Test unpatch
        unpatch()

        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])

        # Test index creation
        es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG['port'])
        Pin(service=self.TEST_SERVICE, tracer=tracer).onto(es)

        # Test index creation
        es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
