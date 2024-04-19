import datetime
from importlib import import_module

import pytest

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.elasticsearch.patch import get_version
from ddtrace.contrib.elasticsearch.patch import get_versions
from ddtrace.contrib.elasticsearch.patch import patch
from ddtrace.contrib.elasticsearch.patch import unpatch
from ddtrace.ext import http
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.contrib.patch import emit_integration_and_version_to_test_agent
from tests.utils import TracerTestCase

from ..config import ELASTICSEARCH_CONFIG


module_names = (
    "elasticsearch",
    "elasticsearch1",
    "elasticsearch2",
    "elasticsearch5",
    "elasticsearch6",
    "elasticsearch7",
    "elasticsearch8",
    "opensearchpy",
)

for module_name in module_names:
    try:
        elasticsearch = import_module(module_name)
        break
    except ImportError:
        pass
else:
    raise ImportError("could not import any of {0!r}".format(module_names))


class ElasticsearchPatchTest(TracerTestCase):
    """
    Elasticsearch integration test suite.
    Need a running ElasticSearch.
    Test cases with patching.
    Will merge when patching will be the default/only way.
    """

    ES_INDEX = "ddtrace_index"
    ES_TYPE = "_doc"
    ES_MAPPING = {"properties": {"name": {"type": "keyword"}, "created": {"type": "date", "format": "yyyy-MM-dd"}}}

    def create_index(self, es):
        if elasticsearch.__version__ >= (8, 0, 0):
            es.options(ignore_status=400).indices.create(index=self.ES_INDEX, mappings=self.ES_MAPPING)
        else:
            es.indices.create(index=self.ES_INDEX, ignore=400, body={"mappings": self.ES_MAPPING})

    def delete_index(self, es):
        if elasticsearch.__version__ >= (8, 0, 0):
            es.options(ignore_status=[400, 404]).indices.delete(index=self.ES_INDEX)
        else:
            es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def setUp(self):
        """Prepare ES"""
        super(ElasticsearchPatchTest, self).setUp()

        es = self._get_es()
        tags = {
            # `component` is a reserved tag. Setting it via `Pin` should have no effect.
            "component": "foo",
            # `custom_tag` is a custom tag that can be set via `Pin`.
            "custom_tag": "bar",
        }
        Pin(tracer=self.tracer, tags=tags).onto(es.transport)
        self.create_index(es)

        patch()

        self.es = es

    def tearDown(self):
        """Clean ES"""
        super(ElasticsearchPatchTest, self).tearDown()

        unpatch()
        self.delete_index(self.es)

    def test_elasticsearch(self):
        es = self.es
        self.create_index(es)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.service == "elasticsearch"
        assert span.name == "elasticsearch.query"
        assert span.span_type == "elasticsearch"
        assert span.error == 0
        assert span.get_tag("elasticsearch.method") == "PUT"
        assert span.get_tag("component") == "elasticsearch"
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("elasticsearch.url") == "/%s" % self.ES_INDEX
        assert span.get_tag("out.host") == "localhost"
        assert span.get_tag("custom_tag") == "bar"
        assert span.resource == "PUT /%s" % self.ES_INDEX

        args = self._get_index_args()
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
        if (7, 0, 0) <= elasticsearch.__version__ < (7, 5, 0):
            assert span.get_tag("elasticsearch.method") == "POST"
            assert span.resource == "POST /%s/%s/?" % (self.ES_INDEX, self.ES_TYPE)
        else:
            assert span.get_tag("elasticsearch.method") == "PUT"
            assert span.resource == "PUT /%s/%s/?" % (self.ES_INDEX, self.ES_TYPE)
        assert span.get_tag("elasticsearch.url") == "/%s/%s/%s" % (self.ES_INDEX, self.ES_TYPE, 10)

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
        assert span.get_tag("component") == "elasticsearch"
        assert span.get_tag("span.kind") == "client"

        # search data
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
        method, url = span.resource.split(" ")
        assert method == span.get_tag("elasticsearch.method")
        assert method in ["GET", "POST"]
        assert self.ES_INDEX in url
        assert url.endswith("/_search")
        assert url == span.get_tag("elasticsearch.url")
        if elasticsearch.__version__ >= (8, 0, 0):
            assert span.get_tag("elasticsearch.body").replace(" ", "") == '{"query":{"match_all":{}},"size":100}'
            assert set(span.get_tag("elasticsearch.params").split("&")) == {"sort=name%3Adesc"}
            assert set(span.get_tag(http.QUERY_STRING).split("&")) == {"sort=name%3Adesc"}
        else:
            assert span.get_tag("elasticsearch.body").replace(" ", "") == '{"query":{"match_all":{}}}'
            assert set(span.get_tag("elasticsearch.params").split("&")) == {"sort=name%3Adesc", "size=100"}
            assert set(span.get_tag(http.QUERY_STRING).split("&")) == {"sort=name%3Adesc", "size=100"}
        assert span.get_tag("component") == "elasticsearch"
        assert span.get_tag("span.kind") == "client"

        self.assertTrue(span.get_metric("elasticsearch.took") > 0)

        # Search by type not supported by default json encoder
        query = {"range": {"created": {"gte": datetime.date(2016, 2, 1)}}}
        result = es.search(size=100, body={"query": query}, **args)

        assert len(result["hits"]["hits"]) == 2, result

    def test_analytics_default(self):
        es = self.es
        self.create_index(es)

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertIsNone(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_with_rate(self):
        with self.override_config("elasticsearch", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            es = self.es
            self.create_index(es)

            spans = self.get_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_without_rate(self):
        with self.override_config("elasticsearch", dict(analytics_enabled=True)):
            es = self.es
            self.create_index(es)

            spans = self.get_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)

    def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        es = self._get_es()
        Pin(tracer=self.tracer).onto(es.transport)

        # Test index creation
        self.create_index(es)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        self.reset()
        unpatch()

        es = self._get_es()

        # Test index creation
        self.create_index(es)

        spans = self.get_spans()
        self.reset()
        assert not spans, spans

        # Test patch again
        self.reset()
        patch()

        es = self._get_es()
        Pin(tracer=self.tracer).onto(es.transport)

        # Test index creation
        self.create_index(es)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0(self):
        """
        v0: When a user specifies a service for the app
            The elasticsearch integration should not use it.
        """
        assert config.service == "mysvc"

        self.create_index(self.es)
        spans = self.get_spans()
        self.reset()
        assert len(spans) == 1
        assert spans[0].service != "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1(self):
        """
        v1: When a user specifies a service for the app
            The elasticsearch integration should use it.
        """
        assert config.service == "mysvc"

        self.create_index(self.es)
        Pin(service="es", tracer=self.tracer).onto(self.es.transport)
        spans = self.get_spans()
        self.reset()
        assert len(spans) == 1
        assert spans[0].service == "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_unspecified_service_v0(self):
        self.create_index(self.es)
        spans = self.get_spans()
        self.reset()
        assert len(spans) == 1
        assert spans[0].service == "elasticsearch"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_unspecified_service_v1(self):
        self.create_index(self.es)
        Pin(service="es", tracer=self.tracer).onto(self.es.transport)
        spans = self.get_spans()
        self.reset()
        assert len(spans) == 1
        assert spans[0].service == DEFAULT_SPAN_SERVICE_NAME

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE_MAPPING="elasticsearch:custom-elasticsearch"))
    def test_service_mapping_config(self):
        """
        When a user specifies a service mapping it should override the default
        """
        assert config.elasticsearch.service != "custom-elasticsearch"

        self.create_index(self.es)
        spans = self.get_spans()
        self.reset()
        assert len(spans) == 1
        assert spans[0].service == "custom-elasticsearch"

    def test_service_name_config_override(self):
        """
        When a user specifies a service mapping it should override the default
        """
        with self.override_config("elasticsearch", dict(service="test_service")):
            self.create_index(self.es)
            spans = self.get_spans()
            self.reset()
            assert len(spans) == 1
            assert spans[0].service == "test_service"

    def test_none_param(self):
        try:
            self.es.transport.perform_request("GET", "/test-index")
        except elasticsearch.exceptions.NotFoundError:
            pass
        spans = self.get_spans()
        assert len(spans) == 1

    def _get_es(self):
        es = elasticsearch.Elasticsearch(hosts=["http://localhost:%d" % ELASTICSEARCH_CONFIG["port"]])
        if elasticsearch.__version__ < (5, 0, 0):
            es.transport.get_connection().headers["content-type"] = "application/json"
        return es

    def _get_index_args(self):
        if elasticsearch.__version__ >= (7, 0, 0):
            return {"index": self.ES_INDEX}
        return {"index": self.ES_INDEX, "doc_type": self.ES_TYPE}

    @pytest.mark.skipif(
        (7, 0, 0) <= elasticsearch.__version__ <= (7, 1, 0), reason="test isn't compatible these elasticsearch versions"
    )
    def test_large_body(self):
        """
        Ensure large bodies are omitted to prevent large traces from being produced.
        """
        args = self._get_index_args()
        body = {
            "query": {"range": {"created": {"gte": "asdf" * 25000}}},
        }
        # it doesn't matter if the request fails, so long as a span is generated
        try:
            self.es.search(size=100, body=body, **args)
        except Exception:
            pass
        spans = self.get_spans()
        self.reset()
        assert len(spans) == 1
        assert len(spans[0].get_tag("elasticsearch.body")) < 25000

    def test_and_emit_get_version(self):
        version = get_version()
        assert type(version) == str
        assert version == ""

        versions = get_versions()
        assert len(versions) > 0
        for module_name, v in versions.items():
            emit_integration_and_version_to_test_agent("elasticsearch", v, module_name=module_name)
