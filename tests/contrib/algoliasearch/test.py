import asyncio
from copy import deepcopy

from ddtrace import config
from ddtrace._monkey import _patch_all
from ddtrace.contrib.internal.algoliasearch.patch import algoliasearch_version
from ddtrace.contrib.internal.algoliasearch.patch import patch
from ddtrace.contrib.internal.algoliasearch.patch import unpatch
from ddtrace.vendor.packaging.version import parse as parse_version
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured


V1 = parse_version("1.0")
V2 = parse_version("2.0")
V4 = parse_version("4.0")
_UNSET = object()


DUMMY_RESULT = {
    "hits": [{"objectID": "dummy-id", "dummy": "dummy"}],
    "processingTimeMS": 23,
    "nbHits": 1,
    "hitsPerPage": 20,
    "exhaustiveNbHits": True,
    "params": "query=xxx",
    "nbPages": 1,
    "query": "xxx",
    "page": 0,
}


def _v4_search_response():
    from algoliasearch.search.models.search_response import SearchResponse

    return SearchResponse.from_dict(deepcopy(DUMMY_RESULT))


def _v4_search_responses():
    from algoliasearch.search.models.search_responses import SearchResponses

    return SearchResponses.from_dict({"results": [deepcopy(DUMMY_RESULT)]})


class AlgoliasearchTest(TracerTestCase):
    def setUp(self):
        super(AlgoliasearchTest, self).setUp()

        # dummy values
        def search(self, query, args=None, request_options=None):
            return dict(DUMMY_RESULT)

        # Algolia search is a non free SaaS application, it isn't possible to add it to the
        # docker environment to enable a full-fledged integration test. The next best option
        # is to mock out the search method to prevent it from making server requests
        if algoliasearch_version < V2 and algoliasearch_version >= V1:
            import algoliasearch
            import algoliasearch.index as index_module

            index_module.Index.search = search
            client = algoliasearch.algoliasearch.Client("X", "X")
            self.index = client.init_index("test_index")
        elif algoliasearch_version >= V2 and algoliasearch_version < V4:
            from algoliasearch.search_client import SearchClient
            import algoliasearch.search_index as index_module

            index_module.SearchIndex.search = search
            client = SearchClient.create("X", "X")
            self.index = client.init_index("test_index")
        else:
            # algoliasearch 4.x — the SearchIndex/init_index() API is gone; the
            # modern surface is SearchClientSync.search_single_index().
            from algoliasearch.search.client import SearchClientSync

            def search_single_index(self, index_name, search_params=None, request_options=None):
                return _v4_search_response()

            def search_many(self, search_method_params, request_options=None):
                return _v4_search_responses()

            SearchClientSync.search_single_index = search_single_index
            SearchClientSync.search = search_many
            self.client = SearchClientSync("APP_ID", "API_KEY")

    def patch_algoliasearch(self):
        patch()

    def tearDown(self):
        super(AlgoliasearchTest, self).tearDown()
        unpatch()
        if hasattr(self, "tracer"):
            self.reset()

    def perform_search(self, query_text, query_args=None):
        if algoliasearch_version < V2 and algoliasearch_version >= V1:
            self.index.search(query_text, args=query_args)
        elif algoliasearch_version >= V2 and algoliasearch_version < V4:
            self.index.search(query_text, request_options=query_args)
        else:
            # v4 combines query text and query args into the single
            # ``search_params`` argument.
            params = dict(query_args) if query_args else {}
            params["query"] = query_text
            self.client.search_single_index("test_index", search_params=params)

    def assert_search_span(self, query_text=_UNSET, hits_per_page=None):
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert_is_measured(span)
        assert span.name == "algoliasearch.search"
        assert span.span_type == "http"
        assert span.error == 0
        assert span.service == "algoliasearch"
        assert span.get_metric("processing_time_ms") == 23
        assert span.get_metric("number_of_hits") == 1
        assert span.get_tag("component") == "algoliasearch"
        assert span.get_tag("span.kind") == "client"
        if query_text is not _UNSET:
            assert span.get_tag("query.text") == query_text
        if hits_per_page is not None:
            assert span.get_metric("query.args.hits_per_page") == hits_per_page
        return span

    def test_algoliasearch(self):
        self.patch_algoliasearch()
        self.perform_search(
            "test search", {"attributesToRetrieve": "firstname,lastname", "unsupportedTotallyNewArgument": "ignore"}
        )

        span = self.assert_search_span(query_text=None)
        self.reset()

        assert span.get_tag("query.args.attributes_to_retrieve") == "firstname,lastname"
        # Verify that adding new arguments to the search API will simply be ignored and not cause
        # errors
        assert span.get_tag("query.args.unsupported_totally_new_argument") is None

    def test_algoliasearch_with_query_text(self):
        self.patch_algoliasearch()
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        self.perform_search(
            "test search", {"attributesToRetrieve": "firstname,lastname", "unsupportedTotallyNewArgument": "ignore"}
        )
        span = self.assert_search_span(query_text="test search")
        assert span.get_tag("query.args.attributes_to_retrieve") == "firstname,lastname"
        assert span.get_tag("query.args.unsupportedTotallyNewArgument") is None
        config.algoliasearch.collect_query_text = original

    def test_algoliasearch_with_query_args_nontext(self):
        self.patch_algoliasearch()
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        self.perform_search("test search", {"hitsPerPage": 1, "page": 3})
        span = self.assert_search_span(query_text="test search", hits_per_page=1)
        assert span.get_metric("query.args.page") == 3
        config.algoliasearch.collect_query_text = original

    def test_algoliasearch_v4_search_single_index_models(self):
        if algoliasearch_version < V4:
            self.skipTest("SearchClientSync API is only available in algoliasearch >= 4")

        from algoliasearch.search.models.search_params import SearchParams
        from algoliasearch.search.models.search_params_object import SearchParamsObject

        self.patch_algoliasearch()
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        try:
            search_params = SearchParams(actual_instance=SearchParamsObject(query="model search", hitsPerPage=5))
            self.client.search_single_index("test_index", search_params=search_params)
            self.assert_search_span(query_text="model search", hits_per_page=5)
        finally:
            config.algoliasearch.collect_query_text = original

    def test_algoliasearch_v4_search_models(self):
        if algoliasearch_version < V4:
            self.skipTest("SearchClientSync API is only available in algoliasearch >= 4")

        from algoliasearch.search.models.search_for_hits import SearchForHits
        from algoliasearch.search.models.search_method_params import SearchMethodParams
        from algoliasearch.search.models.search_query import SearchQuery

        self.patch_algoliasearch()
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        try:
            search_method_params = SearchMethodParams(
                requests=[
                    SearchQuery(
                        actual_instance=SearchForHits(indexName="test_index", query="multi search", hitsPerPage=6)
                    )
                ]
            )
            self.client.search(search_method_params)
            self.assert_search_span(query_text="multi search", hits_per_page=6)
        finally:
            config.algoliasearch.collect_query_text = original

    def test_algoliasearch_v4_async_search_single_index(self):
        if algoliasearch_version < V4:
            self.skipTest("async SearchClient API is only available in algoliasearch >= 4")

        from algoliasearch.search.client import SearchClient

        async def search_single_index(self, index_name, search_params=None, request_options=None):
            return _v4_search_response()

        SearchClient.search_single_index = search_single_index
        self.patch_algoliasearch()
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        async def perform_search():
            client = SearchClient("APP_ID", "API_KEY")
            await client.search_single_index("test_index", search_params={"query": "async search", "hitsPerPage": 5})

        try:
            asyncio.run(perform_search())
            self.assert_search_span(query_text="async search", hits_per_page=5)
        finally:
            config.algoliasearch.collect_query_text = original

    def test_algoliasearch_v4_async_search(self):
        if algoliasearch_version < V4:
            self.skipTest("async SearchClient API is only available in algoliasearch >= 4")

        from algoliasearch.search.client import SearchClient
        from algoliasearch.search.models.search_for_hits import SearchForHits
        from algoliasearch.search.models.search_method_params import SearchMethodParams
        from algoliasearch.search.models.search_query import SearchQuery

        async def search(self, search_method_params, request_options=None):
            return _v4_search_responses()

        SearchClient.search = search
        self.patch_algoliasearch()
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        async def perform_search():
            client = SearchClient("APP_ID", "API_KEY")
            search_method_params = SearchMethodParams(
                requests=[
                    SearchQuery(
                        actual_instance=SearchForHits(indexName="test_index", query="async multi search", hitsPerPage=7)
                    )
                ]
            )
            await client.search(search_method_params)

        try:
            asyncio.run(perform_search())
            self.assert_search_span(query_text="async multi search", hits_per_page=7)
        finally:
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

        self.perform_search("test search")

        spans = self.get_spans()
        self.reset()
        assert not spans, spans

        # Test patch again
        self.reset()
        patch()

        self.perform_search("test search")

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1

    def test_patch_all_auto_enable(self):
        _patch_all()

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
        self.perform_search("test search")
        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].name == "algoliasearch.search.request"
        unpatch()
