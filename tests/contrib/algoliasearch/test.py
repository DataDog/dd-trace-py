import algoliasearch
import algoliasearch.index as index_module
from ddtrace.contrib.algoliasearch.patch import (SEARCH_SPAN_TYPE, patch,
                                                 unpatch)
from ddtrace import config
from ddtrace.pin import Pin
from tests.base import BaseTracerTestCase


class AlgoliasearchTest(BaseTracerTestCase):
    TEST_SERVICE = 'test'

    def setUp(self):
        super(AlgoliasearchTest, self).setUp()

        # dummy values
        def search(self, query, args=None, request_parameters=None):
            return {
                "hits": [
                    {
                        "dummy": "dummy"
                    }
                ],
                "processingTimeMS": 23,
                "nbHits": 1,
                "hitsPerPage": 20,
                "exhaustiveNbHits": True,
                "params": "query=xxx",
                "nbPages": 1,
                "query": "xxx",
                "page": 0
            }

        # monkey patch search to avoid sending server request
        index_module.Index.search = search
        client = algoliasearch.algoliasearch.Client('X', 'X')
        index = client.init_index('test_index')
        patch()
        Pin.override(index, tracer=self.tracer)

        # use this index only to properly test stuff
        self.index = index

    def tearDown(self):
        super(AlgoliasearchTest, self).tearDown()
        unpatch()

    def reset(self):
        super(AlgoliasearchTest, self).reset()

    def test_algoliasearch(self):
        self.index.search(
            "test search",
            args={"attributesToRetrieve": "firstname,lastname", 'unsupportedTotallyNewArgument': 'ignore'}
        )

        spans = self.get_spans()
        self.reset()

        assert len(spans) == 1
        span = spans[0]
        assert span.service == 'algoliasearch'
        assert span.name == 'algoliasearch.search'
        assert span.span_type == SEARCH_SPAN_TYPE
        assert span.error == 0
        assert span.get_tag('query_args.attributes_to_retrieve') == "firstname,lastname"
        # Verify that adding new arguments to the search API will simply be ignored and not cause
        # errors
        assert span.get_tag('query_args.unsupported_totally_new_argument') is None
        assert span.get_metric('processing_time_ms') == 23
        assert span.get_metric('number_of_hits') == 1
        # Verify that query_text does not get passed along
        assert span.get_tag('query_text') is None

        config.algoliasearch.collect_query_text = True

        self.index.search(
            "test search",
            args={"attributesToRetrieve": "firstname,lastname", 'unsupportedTotallyNewArgument': 'ignore'}
        )
        spans = self.get_spans()
        self.reset()
        span = spans[0]
        # Verify that query_text does not get passed along
        assert span.get_tag('query_text') == "test search"

    def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        self.index.search("test search")

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        self.index.search("test search")

        spans = self.get_spans()
        self.reset()
        assert not spans, spans

        # Test patch again
        self.reset()
        patch()

        self.index.search("test search")

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1
