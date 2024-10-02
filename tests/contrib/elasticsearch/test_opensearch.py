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

    def _get_es_config(self):
        return OPENSEARCH_CONFIG

    def _get_es(self):
        return opensearchpy.OpenSearch(host=OPENSEARCH_CONFIG["host"], port=OPENSEARCH_CONFIG["port"])

    def _get_index_args(self):
        if opensearchpy.VERSION < (1, 1):
            return {"index": self.ES_INDEX, "doc_type": self.ES_TYPE}
        return {"index": self.ES_INDEX}
