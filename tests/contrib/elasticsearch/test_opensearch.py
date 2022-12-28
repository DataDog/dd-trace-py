import opensearchpy

from tests.contrib.config import OPENSEARCH_CONFIG

from .test_elasticsearch import ElasticsearchPatchTest


class OpenSearchPatchTest(ElasticsearchPatchTest):
    """
    Elasticsearch integration test suite.
    Need a running ElasticSearch.
    Test cases with patching.
    Will merge when patching will be the default/only way.
    """

    ES_INDEX = "ddtrace_index"
    ES_TYPE = "_doc"
    ES_MAPPING = {
        "mappings": {"properties": {"name": {"type": "keyword"}, "created": {"type": "date", "format": "yyyy-MM-dd"}}}
    }

    def _get_es(self):
        return opensearchpy.OpenSearch(port=OPENSEARCH_CONFIG["port"])

    def _get_index_args(self):
        if opensearchpy.VERSION < (1, 1):
            return {"index": self.ES_INDEX, "doc_type": self.ES_TYPE}
        return {"index": self.ES_INDEX}
