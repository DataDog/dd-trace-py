import datetime

import opensearchpy

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.opensearch.patch import patch
from ddtrace.contrib.opensearch.patch import unpatch
from ddtrace.ext import http
from tests.utils import TracerTestCase

from ..config import OPENSEARCH_CONFIG


class OpenSearchPatchTest(TracerTestCase):
    """
    OpenSearch integration test suite.
    Need a running OpenSearch.
    Test cases with patching.
    Will merge when patching will be the default/only way.
    """

    OS_INDEX = "ddtrace_index"
    OS_TYPE = "_doc"
    OS_MAPPING = {
        "mappings": {"properties": {"name": {"type": "keyword"}, "created": {"type": "date", "format": "yyyy-MM-dd"}}}
    }

    TEST_PORT = str(OPENSEARCH_CONFIG["port"])

    def setUp(self):
        """Prepare OpenSearch"""
        super(OpenSearchPatchTest, self).setUp()

        os = opensearchpy.OpenSearch(port=OPENSEARCH_CONFIG["port"])
        Pin(tracer=self.tracer).onto(os.transport)
        os.indices.create(index=self.OS_INDEX, ignore=400, body=self.OS_MAPPING)

        patch()

        self.os = os

    def tearDown(self):
        """Clean OpenSearch"""
        super(OpenSearchPatchTest, self).tearDown()

        unpatch()
        self.os.indices.delete(index=self.OS_INDEX, ignore=[400, 404])

    def test_opensearch(self):
        os = self.os
        os.indices.create(index=self.OS_INDEX, ignore=400, body=self.OS_MAPPING)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.service == "opensearch"
        assert span.name == "opensearch.query"
        assert span.span_type == "elasticsearch"
        assert span.error == 0
        assert span.get_tag("opensearch.method") == "PUT"
        assert span.get_tag("opensearch.url") == "/%s" % self.OS_INDEX
        assert span.resource == "PUT /%s" % self.OS_INDEX

        args = {"index": self.OS_INDEX}
        os.index(id=10, body={"name": "ten", "created": datetime.date(2016, 1, 1)}, **args)
        os.index(id=11, body={"name": "eleven", "created": datetime.date(2016, 2, 1)}, **args)
        os.index(id=12, body={"name": "twelve", "created": datetime.date(2016, 3, 1)}, **args)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 3
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.error == 0
        assert span.get_tag("opensearch.method") == "PUT"
        assert span.resource == "PUT /%s/%s/?" % (self.OS_INDEX, self.OS_TYPE)
        assert span.get_tag("opensearch.url") == "/%s/%s/%s" % (self.OS_INDEX, self.OS_TYPE, 10)

        os.indices.refresh(index=self.OS_INDEX)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.resource == "POST /%s/_refresh" % self.OS_INDEX
        assert span.get_tag("opensearch.method") == "POST"
        assert span.get_tag("opensearch.url") == "/%s/_refresh" % self.OS_INDEX

        # search data
        args = {"index": self.OS_INDEX}
        with self.override_http_config("opensearch", dict(trace_query_string=True)):
            os.index(id=10, body={"name": "ten", "created": datetime.date(2016, 1, 1)}, **args)
            os.index(id=11, body={"name": "eleven", "created": datetime.date(2016, 2, 1)}, **args)
            os.index(id=12, body={"name": "twelve", "created": datetime.date(2016, 3, 1)}, **args)
            result = os.search(sort=["name:desc"], size=100, body={"query": {"match_all": {}}}, **args)

        assert len(result["hits"]["hits"]) == 3, result
        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 4
        span = spans[-1]
        TracerTestCase.assert_is_measured(span)
        method, url = span.resource.split(" ")
        assert method == span.get_tag("opensearch.method")
        assert method in ["GET", "POST"]
        assert self.OS_INDEX in url
        assert url.endswith("/_search")
        assert url == span.get_tag("opensearch.url")
        assert span.get_tag("opensearch.body").replace(" ", "") == '{"query":{"match_all":{}}}'
        assert set(span.get_tag("opensearch.params").split("&")) == {"sort=name%3Adesc", "size=100"}
        assert set(span.get_tag(http.QUERY_STRING).split("&")) == {"sort=name%3Adesc", "size=100"}

        self.assertTrue(span.get_metric("opensearch.took") > 0)

        # Search by type not supported by default json encoder
        query = {"range": {"created": {"gte": datetime.date(2016, 2, 1)}}}
        result = os.search(size=100, body={"query": query}, **args)

        assert len(result["hits"]["hits"]) == 2, result

    def test_analytics_default(self):
        self.os.indices.create(index=self.OS_INDEX, ignore=400, body=self.OS_MAPPING)

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertIsNone(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_with_rate(self):
        with self.override_config("opensearch", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            self.os.indices.create(index=self.OS_INDEX, ignore=400, body=self.OS_MAPPING)

            spans = self.get_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_without_rate(self):
        with self.override_config("opensearch", dict(analytics_enabled=True)):
            self.os.indices.create(index=self.OS_INDEX, ignore=400, body=self.OS_MAPPING)

            spans = self.get_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)

    def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        os = opensearchpy.OpenSearch(port=OPENSEARCH_CONFIG["port"])
        Pin(tracer=self.tracer).onto(os.transport)

        # Test index creation
        os.indices.create(index=self.OS_INDEX, ignore=400)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        self.reset()
        unpatch()

        os = opensearchpy.OpenSearch(port=OPENSEARCH_CONFIG["port"])

        # Test index creation
        os.indices.create(index=self.OS_INDEX, ignore=400)

        spans = self.get_spans()
        self.reset()
        assert not spans, spans

        # Test patch again
        self.reset()
        patch()

        os = opensearchpy.OpenSearch(port=OPENSEARCH_CONFIG["port"])
        Pin(tracer=self.tracer).onto(os.transport)

        # Test index creation
        os.indices.create(index=self.OS_INDEX, ignore=400)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service(self):
        """
        When a user specifies a service for the app
            The opensearch integration should not use it.
        """
        assert config.service == "mysvc"

        self.os.indices.create(index=self.OS_INDEX, ignore=400)
        Pin(service="os", tracer=self.tracer).onto(self.os.transport)
        spans = self.get_spans()
        self.reset()
        assert len(spans) == 1
        assert spans[0].service != "os"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE_MAPPING="opensearch:custom-opensearch"))
    def test_service_mapping_config(self):
        """
        When a user specifies a service mapping it should override the default
        """
        assert config.opensearch.service != "custom-opensearch"

        self.os.indices.create(index=self.OS_INDEX, ignore=400)
        spans = self.get_spans()
        self.reset()
        assert len(spans) == 1
        assert spans[0].service == "custom-opensearch"

    def test_service_name_config_override(self):
        """
        When a user specifies a service mapping it should override the default
        """
        with self.override_config("opensearch", dict(service="test_service")):
            self.os.indices.create(index=self.OS_INDEX, ignore=400)
            spans = self.get_spans()
            self.reset()
            assert len(spans) == 1
            assert spans[0].service == "test_service"

    def test_none_param(self):
        try:
            self.os.transport.perform_request("GET", "/test-index", body="{}", params=None)
        except opensearchpy.exceptions.NotFoundError:
            pass
        spans = self.get_spans()
        assert len(spans) == 1
