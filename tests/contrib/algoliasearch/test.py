import asyncio
import importlib
import json
import sys
from unittest import mock

import pytest

from ddtrace import config
from ddtrace._monkey import _patch_all
from ddtrace.contrib.internal.algoliasearch.patch import V4
from ddtrace.contrib.internal.algoliasearch.patch import algoliasearch_version
from ddtrace.contrib.internal.algoliasearch.patch import patch
from ddtrace.contrib.internal.algoliasearch.patch import unpatch
from ddtrace.vendor.packaging.version import parse as parse_version
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured


V1 = parse_version("1.0")
V2 = parse_version("2.0")

SEARCH_RESPONSE = {
    "hits": [{"objectID": "1", "dummy": "dummy"}],
    "processingTimeMS": 23,
    "nbHits": 1,
    "hitsPerPage": 20,
    "exhaustiveNbHits": True,
    "params": "query=xxx",
    "nbPages": 1,
    "query": "xxx",
    "page": 0,
}


class V4Index:
    """
    Presents a 4.x client through the Index.search() interface of the older clients, which
    let the tests below stay agnostic of the client generation.
    """

    def __init__(self, client, index_name):
        self.client = client
        self.index_name = index_name

    def search(self, query, args=None, request_options=None):
        return self.client.search_single_index(self.index_name, dict(args or {}, query=query))


def make_v4_client(sync=True):
    from algoliasearch.http.api_response import ApiResponse
    from algoliasearch.http.verb import Verb
    from algoliasearch.search.client import SearchClient
    from algoliasearch.search.client import SearchClientSync

    response = ApiResponse(verb=Verb.POST, raw_data=json.dumps(SEARCH_RESPONSE), status_code=200)

    # The transporter is the network boundary of the generated client, so stubbing it keeps
    # the rest of the real search_single_index() path, including the deserialization into a
    # SearchResponse, under test.
    if sync:
        client = SearchClientSync("X", "X")
        client._transporter.request = lambda **kwargs: response
    else:
        client = SearchClient("X", "X")

        async def request(**kwargs):
            return response

        client._transporter.request = request
    return client


class AlgoliasearchTest(TracerTestCase):
    def setUp(self):
        super().setUp()

        # dummy values
        def search(self, query, args=None, request_options=None):
            return dict(SEARCH_RESPONSE)

        # Algolia search is a non free SaaS application, it isn't possible to add it to the
        # docker environment to enable a full-fledged integration test. The next best option
        # is to mock out the search method to prevent it from making server requests
        if algoliasearch_version < V2 and algoliasearch_version >= V1:
            import algoliasearch
            import algoliasearch.index as index_module

            index_module.Index.search = search
            client = algoliasearch.algoliasearch.Client("X", "X")
            # use this index only to properly test stuff
            self.index = client.init_index("test_index")
        elif algoliasearch_version >= V4:
            self.index = V4Index(make_v4_client(), "test_index")
        else:
            from algoliasearch.search_client import SearchClient
            import algoliasearch.search_index as index_module

            index_module.SearchIndex.search = search
            client = SearchClient.create("X", "X")
            self.index = client.init_index("test_index")

    def patch_algoliasearch(self):
        patch()

    def tearDown(self):
        super().tearDown()
        unpatch()
        if hasattr(self, "tracer"):
            self.reset()

    def perform_search(self, query_text, query_args=None):
        if (algoliasearch_version < V2 and algoliasearch_version >= V1) or algoliasearch_version >= V4:
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

    def test_algoliasearch_with_query_text_as_keyword(self):
        self.patch_algoliasearch()
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        try:
            # wrapt passes the instance separately, so a keyword-only call leaves no
            # positional arguments for the query text lookup to read.
            self.index.search(query="test search")
        finally:
            config.algoliasearch.collect_query_text = original

        spans = self.get_spans()
        assert len(spans) == 1
        assert spans[0].get_tag("query.text") == "test search"

    def test_algoliasearch_with_empty_query_text(self):
        self.patch_algoliasearch()
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        try:
            # An empty query is a valid search that returns every record, so it has to
            # stay distinguishable from a call that supplied no query at all.
            self.perform_search("")
        finally:
            config.algoliasearch.collect_query_text = original

        spans = self.get_spans()
        assert len(spans) == 1
        assert spans[0].get_tag("query.text") == ""

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

    @pytest.mark.skipif(algoliasearch_version < V4, reason="requires the algoliasearch 4.x client")
    def test_algoliasearch_v4_search_params_models(self):
        from algoliasearch.search.models.search_params import SearchParams
        from algoliasearch.search.models.search_params_object import SearchParamsObject
        from algoliasearch.search.models.search_params_string import SearchParamsString

        self.patch_algoliasearch()
        client = make_v4_client()
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        try:
            params = SearchParamsObject(query="test search", hits_per_page=1, attributes_to_retrieve=["firstname"])
            client.search_single_index("test_index", params)
            client.search_single_index("test_index", SearchParams(actual_instance=params))
            client.search_single_index("test_index", SearchParamsString(params="query=test+search&hitsPerPage=1"))
            client.search_single_index("test_index")
        finally:
            config.algoliasearch.collect_query_text = original

        spans = self.get_spans()
        assert len(spans) == 4
        for span in spans[:2]:
            assert span.get_tag("query.text") == "test search"
            assert span.get_metric("query.args.hits_per_page") == 1
            assert span.get_tag("query.args.attributes_to_retrieve") == "['firstname']"
        # A pre-encoded params string is not parsed, and a call without params has nothing to tag.
        for span in spans[2:]:
            assert span.get_tag("query.text") is None
            assert span.get_metric("query.args.hits_per_page") is None
            assert span.get_metric("processing_time_ms") == 23
            assert span.get_metric("number_of_hits") == 1

    @pytest.mark.skipif(algoliasearch_version < V4, reason="requires the algoliasearch 4.x client")
    def test_algoliasearch_v4_async(self):
        self.patch_algoliasearch()
        client = make_v4_client(sync=False)
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        try:
            response = asyncio.run(
                client.search_single_index("test_index", {"query": "test search", "hitsPerPage": 1, "page": 3})
            )
        finally:
            config.algoliasearch.collect_query_text = original

        assert response.nb_hits == 1
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert_is_measured(span)
        assert span.name == "algoliasearch.search"
        assert span.span_type == "http"
        assert span.error == 0
        assert span.service == "algoliasearch"
        assert span.get_tag("component") == "algoliasearch"
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("query.text") == "test search"
        assert span.get_metric("query.args.hits_per_page") == 1
        assert span.get_metric("query.args.page") == 3
        assert span.get_metric("processing_time_ms") == 23
        assert span.get_metric("number_of_hits") == 1

    @pytest.mark.skipif(algoliasearch_version < V4, reason="requires the algoliasearch 4.x client")
    def test_algoliasearch_v4_async_error_and_unpatch(self):
        self.patch_algoliasearch()
        client = make_v4_client(sync=False)

        async def fail(**kwargs):
            raise RuntimeError("boom")

        client._transporter.request = fail

        with pytest.raises(RuntimeError):
            asyncio.run(client.search_single_index("test_index", {"query": "test search"}))

        spans = self.get_spans()
        self.reset()
        assert len(spans) == 1
        assert spans[0].error == 1

        # unpatching has to cover the async client too
        unpatch()
        with pytest.raises(RuntimeError):
            asyncio.run(client.search_single_index("test_index", {"query": "test search"}))
        assert not self.get_spans()

    @pytest.mark.skipif(algoliasearch_version < V4, reason="requires the algoliasearch 4.x client")
    def test_algoliasearch_v4_keyword_arguments(self):
        self.patch_algoliasearch()
        client = make_v4_client()
        original = config.algoliasearch.collect_query_text
        config.algoliasearch.collect_query_text = True

        try:
            client.search_single_index(index_name="test_index", search_params={"query": "test search", "page": 2})
        finally:
            config.algoliasearch.collect_query_text = original

        spans = self.get_spans()
        assert len(spans) == 1
        assert spans[0].get_tag("query.text") == "test search"
        assert spans[0].get_metric("query.args.page") == 2

    @pytest.mark.skipif(algoliasearch_version < V4, reason="requires the algoliasearch 4.x client")
    def test_algoliasearch_v4_error(self):
        self.patch_algoliasearch()
        client = make_v4_client()

        def fail(**kwargs):
            raise RuntimeError("boom")

        client._transporter.request = fail

        with pytest.raises(RuntimeError):
            client.search_single_index("test_index", {"query": "test search"})

        spans = self.get_spans()
        assert len(spans) == 1
        assert spans[0].error == 1

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


def _reload_patch_module():
    return importlib.reload(sys.modules["ddtrace.contrib.internal.algoliasearch.patch"])


# get_version() has to return a string: the telemetry writer rejects other types and the
# resulting TypeError escapes into whichever import triggered patching, taking the
# application down with it.
def test_get_version_without_version_submodule():
    # algoliasearch >= 4 replaced the version submodule with a package level __version__.
    import algoliasearch

    try:
        with (
            mock.patch.dict(sys.modules, {"algoliasearch.version": None}),
            mock.patch.object(algoliasearch, "__version__", "4.44.4", create=True),
        ):
            module = _reload_patch_module()
            assert module.get_version() == "4.44.4"
    finally:
        _reload_patch_module()


def test_get_version_without_algoliasearch_installed():
    try:
        with mock.patch.dict(sys.modules, {"algoliasearch": None}):
            module = _reload_patch_module()
            assert module.get_version() == ""
    finally:
        _reload_patch_module()


def test_upper_bound_excludes_4x_prereleases():
    for version in ("4.0.0a4", "4.0.0b31", "4.0.0", "4.47.0"):
        assert parse_version(version) >= V4, version

    for version in ("2.6.3", "3.0.0", "3.0.0b1", "3.9.9"):
        assert parse_version(version) < V4, version
