from google.pubsub_v1.types import DeleteTopicRequest
from google.pubsub_v1.types import Topic
import pytest


PROJECT_PATH = "projects/test-project"


def _assert_admin_span(span, method, resource_path, project_id="test-project"):
    assert span.name == "gcp.pubsub.request"
    assert span.resource == "{} {}".format(method, resource_path)
    assert span.get_tag("component") == "google_cloud_pubsub"
    assert span.get_tag("span.kind") == "client"
    assert span.get_tag("gcloud.project_id") == project_id
    assert span.get_tag("pubsub.method") == method
    assert span.get_metric("_dd.measured") == 1
    assert span.get_tag("messaging.system") is None
    assert span.get_tag("messaging.operation") is None


def _assert_create_delete_spans(test_spans, topic_name):
    create_span = test_spans.find_span(name="gcp.pubsub.request", resource="createTopic {}".format(topic_name))
    _assert_admin_span(create_span, "createTopic", topic_name)

    delete_span = test_spans.find_span(name="gcp.pubsub.request", resource="deleteTopic {}".format(topic_name))
    _assert_admin_span(delete_span, "deleteTopic", topic_name)


class TestAdminOperations:
    def test_flat_kwargs(self, publisher, test_spans):
        topic_name = "{}/topics/admin-test-flat-kwargs".format(PROJECT_PATH)
        publisher.create_topic(name=topic_name)
        publisher.delete_topic(topic=topic_name)
        _assert_create_delete_spans(test_spans, topic_name)

    def test_positional_request_arg(self, publisher, test_spans):
        topic_name = "{}/topics/admin-test-positional".format(PROJECT_PATH)
        publisher.create_topic(Topic(name=topic_name))
        publisher.delete_topic(DeleteTopicRequest(topic=topic_name))
        _assert_create_delete_spans(test_spans, topic_name)

    def test_keyword_request_arg(self, publisher, test_spans):
        topic_name = "{}/topics/admin-test-keyword".format(PROJECT_PATH)
        publisher.create_topic(request=Topic(name=topic_name))
        publisher.delete_topic(request=DeleteTopicRequest(topic=topic_name))
        _assert_create_delete_spans(test_spans, topic_name)


class TestErrorHandling:
    def test_error_records_on_span(self, publisher, test_spans):
        with pytest.raises(Exception):
            publisher.get_topic(topic="{}/topics/nonexistent-topic-xyz".format(PROJECT_PATH))

        span = test_spans.find_span(name="gcp.pubsub.request")
        assert span.error == 1
        assert span.get_tag("error.type") is not None
