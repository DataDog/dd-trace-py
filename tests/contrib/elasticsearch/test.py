import unittest

from ddtrace.contrib.elasticsearch import missing_modules

if missing_modules:
    raise unittest.SkipTest("Missing dependencies %s" % missing_modules)

import elasticsearch
from nose.tools import eq_

from ddtrace.tracer import Tracer
from ddtrace.contrib.elasticsearch import get_traced_transport, metadata

from ...test_tracer import DummyWriter


class ElasticsearchTest(unittest.TestCase):
    """Elasticsearch integration test suite

    Need a running ES on localhost:9200
    """

    ES_INDEX = 'ddtrace_index'
    ES_TYPE = 'ddtrace_type'

    TEST_SERVICE = 'test'

    def setUp(self):
        """Prepare ES"""
        es = elasticsearch.Elasticsearch()
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def tearDown(self):
        """Clean ES"""
        es = elasticsearch.Elasticsearch()
        es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def test_elasticsearch(self):
        """Test the elasticsearch integration

        All in this for now. Will split it later.
        """
        writer = DummyWriter()
        tracer = Tracer()
        tracer.writer = writer
        transport_class = get_traced_transport(datadog_tracer=tracer, datadog_service=self.TEST_SERVICE)

        es = elasticsearch.Elasticsearch(transport_class=transport_class)

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
        es.index(index=self.ES_INDEX, doc_type=self.ES_TYPE, id=10, body={'name': 'ten'})
        es.index(index=self.ES_INDEX, doc_type=self.ES_TYPE, id=11, body={'name': 'eleven'})
        es.index(index=self.ES_INDEX, doc_type=self.ES_TYPE, id=12, body={'name': 'twelve'})

        spans = writer.pop()
        assert spans
        eq_(len(spans), 3)
        span = spans[0]
        eq_(span.error, 0)
        eq_(span.get_tag(metadata.METHOD), "PUT")
        eq_(span.get_tag(metadata.URL), "/%s/%s/%s" % (self.ES_INDEX, self.ES_TYPE, 10))
        eq_(span.resource, "PUT /%s/%s/?" % (self.ES_INDEX, self.ES_TYPE))

        # Search data
        es.search(index=self.ES_INDEX, doc_type=self.ES_TYPE, sort=['name:desc'], size=100, body={"query":{"match_all":{}}})

        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.resource, "GET /%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE))
        eq_(span.get_tag(metadata.METHOD), "GET")
        eq_(span.get_tag(metadata.URL), "/%s/%s/_search" % (self.ES_INDEX, self.ES_TYPE))
        eq_(span.get_tag(metadata.BODY).replace(" ", ""), '{"query":{"match_all":{}}}')
        eq_(set(span.get_tag(metadata.PARAMS).split('&')), {'sort=name%3Adesc', 'size=100'})

        self.assertTrue(span.get_metric(metadata.TOOK) > 0)
