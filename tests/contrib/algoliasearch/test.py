from ddtrace import config
from ddtrace._monkey import _patch_all
from ddtrace.contrib.internal.algoliasearch.patch import algoliasearch_version
from ddtrace.contrib.internal.algoliasearch.patch import patch
from ddtrace.contrib.internal.algoliasearch.patch import unpatch
from ddtrace.trace import Pin
from ddtrace.vendor.packaging.version import parse as parse_version
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured


V1 = parse_version("1.0")
V2 = parse_version("2.0")


class AlgoliasearchTest(TracerTestCase):
    def setUp(self):
        super(AlgoliasearchTest, self).setUp()

        # dummy values
        def search(self, query, args=None, request_options=None):
            return {
                "hits": [{"dummy": "dummy"}],
                "processingTimeMS": 23,
                "nbHits": 1,
                "hitsPerPage": 20,
                "exhaustiveNbHits": True,
                "params": "query=xxx",
                "nbPages": 1,
                "query": "xxx",
                "page": 0,
            }

        # Algolia search is a non free SaaS application, it isn't possible to add it to the
        # docker environment to enable a full-fledged integration test. The next best option
        # is to mock out the search method to prevent it from making server requests
        if algoliasearch_version < V2 and algoliasearch_version >= V1:
            import algoliasearch
            import algoliasearch.index as index_module

            index_module.Index.search = search
            client = algoliasearch.algoliasearch.Client("X", "X")
        else:
            from algoliasearch.search_client import SearchClient
            import algoliasearch.search_index as index_module

            index_module.SearchIndex.search = search
            client = SearchClient.create("X", "X")

        # use this index only to properly test stuff
        self.index = client.init_index("test_index")

    def patch_algoliasearch(self):
        patch()
        Pin._override(self.index, tracer=self.tracer)

    def tearDown(self):
        super(AlgoliasearchTest, self).tearDown()
        unpatch()
        if hasattr(self, "tracer"):
            self.reset()

    def perform_search(self, query_text, query_args=None):
        if algoliasearch_version < V2 and algoliasearch_version >= V1:
            self.index.search(query_text, args=query_args)
        else:
            self.index.search(query_text, request_options=query_args)

    def test_algoliasearch(self):
        self.patch_algoliasearch()
        self.perform_search(
            "test search", {"attributesToRetrieve": "firstname,lastname", "unsupportedTotallyNewArgument": "ignore"}
        )

        spans = self.get_spans()
        self.reset()

        assert len(spans) == 1
        span = spans[0]
        assert_is_measured(span)
        assert span.name == "algoliasearch.search"
        assert span.span_type == "http"
        assert span.error == 0
        assert span.service == "algoliasearch"
        assert span.get_tag("query.args.attributes_to_retrieve") == "firstname,lastname"
        # Verify that adding new arguments to the search API will simply be ignored and not cause
        # errors
        assert span.get_tag("query.args.unsupported_totally_new_argument") is None
        assert span.get_metric("processing_time_ms") == 23
        assert span.get_metric("number_of_hits") == 1
        assert span.get_tag("component") == "algoliasearch"
        assert span.get_tag("span.kind") == "client"

        # Verify query_text, which may contain sensitive data, is not passed along
        # unless the config value is appropriately set
        assert span.get_tag("query.text") is None

    def test_algoliasearch_with_query_text(self):
        self.patch_algoliasearch()
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        self.perform_search(
            "test search", {"attributesToRetrieve": "firstname,lastname", "unsupportedTotallyNewArgument": "ignore"}
        )
        spans = self.get_spans()
        span = spans[0]
        assert span.get_tag("query.text") == "test search"
        assert span.get_tag("query.args.attributes_to_retrieve") == "firstname,lastname"
        assert span.get_tag("query.args.unsupportedTotallyNewArgument") is None
        config.algoliasearch.collect_query_text = original

    def test_algoliasearch_with_query_args_nontext(self):
        self.patch_algoliasearch()
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        self.perform_search("test search", {"hitsPerPage": 1, "page": 3})
        spans = self.get_spans()
        span = spans[0]
        assert span.get_tag("query.text") == "test search"
        assert span.get_metric("query.args.page") == 3
        assert span.get_metric("query.args.hits_per_page") == 1
        config.algoliasearch.collect_query_text = original

    def test_patch_unpatch(self):
        self.patch_algoliasearch()
        # Test patch idempotence
        patch()
        patch()

        self.perform_search("test search")

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

    def test_patch_all_auto_enable(self):
        _patch_all()
        Pin._override(self.index, tracer=self.tracer)

        self.perform_search("test search")

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1

        unpatch()

        self.perform_search("test search")

        spans = self.get_spans()
        assert not spans, spans

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service_default(self):
        """
        When a service name is specified by the user
            The algoliasearch integration shouldn't use it as the service name
        """
        _patch_all()
        Pin._override(self.index, tracer=self.tracer)
        self.perform_search("test search")
        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].service == "algoliasearch"
        unpatch()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0", DD_SERVICE="mysvc"))
    def test_user_specified_service_v0(self):
        """
        When a service name is specified by the user
            The algoliasearch integration shouldn't use it as the service name
        """
        _patch_all()
        Pin._override(self.index, tracer=self.tracer)
        self.perform_search("test search")
        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].service == "algoliasearch"
        unpatch()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1", DD_SERVICE="mysvc"))
    def test_user_specified_service_v1(self):
        """
        In the v1 service name schema, services default to $DD_SERVICE,
            so make sure that is used and not the v0 schema 'algoliasearch'
        """
        _patch_all()
        Pin._override(self.index, tracer=self.tracer)
        self.perform_search("test search")
        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].service == "mysvc"
        unpatch()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_span_name_v0_schema(self):
        _patch_all()
        Pin._override(self.index, tracer=self.tracer)
        self.perform_search("test search")
        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].name == "algoliasearch.search"
        unpatch()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_span_name_v1_schema(self):
        _patch_all()
        Pin._override(self.index, tracer=self.tracer)
        self.perform_search("test search")
        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].name == "algoliasearch.search.request"
        unpatch()
