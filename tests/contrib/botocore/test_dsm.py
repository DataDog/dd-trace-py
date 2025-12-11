import base64
import json
import sys
import unittest

import botocore
import botocore.session
import mock
from moto import mock_kinesis
from moto import mock_sns
from moto import mock_sqs
import pytest

from ddtrace._trace.pin import Pin
from ddtrace._trace.utils_botocore import span_tags
from ddtrace.contrib.internal.botocore.patch import patch
from ddtrace.contrib.internal.botocore.patch import patch_submodules
from ddtrace.contrib.internal.botocore.patch import unpatch
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.utils.version import parse_version
from tests.utils import TracerTestCase


# Parse botocore.__version_ from "1.9.0" to (1, 9, 0)
BOTOCORE_VERSION = parse_version(botocore.__version__)


@pytest.mark.skipif(BOTOCORE_VERSION >= (1, 34, 131), reason="Test is incompatible with botocore>=1.34.131")
class BotocoreDSMTest(TracerTestCase):
    """Botocore DSM (Data Streams Monitoring) integration tests"""

    TEST_SERVICE = "test-botocore-tracing"

    @mock_sqs
    def setUp(self):
        patch()
        patch_submodules(True)

        self.session = botocore.session.get_session()
        self.session.set_credentials(access_key="access-key", secret_key="secret-key")

        self.queue_name = "Test"
        self.sqs_client = self.session.create_client(
            "sqs", region_name="us-east-1", endpoint_url="http://localhost:4566"
        )
        for queue_url in self.sqs_client.list_queues().get("QueueUrls", []):
            self.sqs_client.delete_queue(QueueUrl=queue_url)

        self.sqs_test_queue = self.sqs_client.create_queue(QueueName=self.queue_name)

        super(BotocoreDSMTest, self).setUp()

        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(botocore.parsers.ResponseParser)
        # Setting the validated flag to False ensures the redaction paths configurations are re-validated
        # FIXME: Ensure AWSPayloadTagging._REQUEST_REDACTION_PATHS_DEFAULTS is always in sync with
        # config.botocore.payload_tagging_request
        # FIXME: Ensure AWSPayloadTagging._RESPONSE_REDACTION_PATHS_DEFAULTS is always in sync with
        # config.botocore.payload_tagging_response
        span_tags._PAYLOAD_TAGGER.validated = False

    def tearDown(self):
        super(BotocoreDSMTest, self).tearDown()

        unpatch()
        self.sqs_client.delete_queue(QueueUrl=self.queue_name)

    def _kinesis_create_stream(self, client, stream_name):
        client.create_stream(StreamName=stream_name, ShardCount=1)
        response = client.describe_stream(StreamName=stream_name)
        stream_arn = response["StreamDescription"]["StreamARN"]
        shard_id = response["StreamDescription"]["Shards"][0]["ShardId"]
        return shard_id, stream_arn

    def _kinesis_get_shard_iterator(self, client, stream_name, shard_id):
        response = client.get_shard_iterator(
            StreamName=stream_name,
            ShardId=shard_id,
            ShardIteratorType="TRIM_HORIZON",
        )
        return response["ShardIterator"]

    def _kinesis_generate_records(self, data, count):
        return [{"Data": data, "PartitionKey": str(i)} for i in range(count)]

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_data_streams_sns_to_sqs(self):
        self._test_data_streams_sns_to_sqs(False)

    @mock.patch.object(sys.modules["ddtrace.contrib.internal.botocore.services.sqs"], "_encode_data")
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_data_streams_sns_to_sqs_raw_delivery(self, mock_encode):
        """
        Moto doesn't currently handle raw delivery message handling quite correctly.
        In the real world, AWS will encode data for us. Moto does not.

        So, we patch our code here to encode the data
        """

        def _moto_compatible_encode(trace_data):
            return base64.b64encode(json.dumps(trace_data).encode("utf-8"))

        mock_encode.side_effect = _moto_compatible_encode
        self._test_data_streams_sns_to_sqs(True)

    @mock_sns
    @mock_sqs
    def _test_data_streams_sns_to_sqs(self, use_raw_delivery):
        # DEV: We want to mock time to ensure we only create a single bucket
        with (
            mock.patch("time.time") as mt,
            mock.patch(
                "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
            ),
        ):
            mt.return_value = 1642544540

            sns = self.session.create_client("sns", region_name="us-east-1", endpoint_url="http://localhost:4566")

            topic = sns.create_topic(Name="testTopic")

            topic_arn = topic["TopicArn"]
            sqs_url = self.sqs_test_queue["QueueUrl"]
            url_parts = sqs_url.split("/")
            sqs_arn = "arn:aws:sqs:{}:{}:{}".format("us-east-1", url_parts[-2], url_parts[-1])
            subscription = sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=sqs_arn)

            if use_raw_delivery:
                sns.set_subscription_attributes(
                    SubscriptionArn=subscription["SubscriptionArn"],
                    AttributeName="RawMessageDelivery",
                    AttributeValue="true",
                )

            Pin.get_from(sns)._clone(tracer=self.tracer).onto(sns)
            Pin.get_from(self.sqs_client)._clone(tracer=self.tracer).onto(self.sqs_client)

            sns.publish(TopicArn=topic_arn, Message="test")

            self.get_spans()

            # get SNS messages via SQS
            self.sqs_client.receive_message(QueueUrl=self.sqs_test_queue["QueueUrl"], WaitTimeSeconds=2)

            # clean up resources
            sns.delete_topic(TopicArn=topic_arn)

            pin = Pin.get_from(sns)
            buckets = pin.tracer.data_streams_processor._buckets
            assert len(buckets) == 1, "Expected 1 bucket but found {}".format(len(buckets))
            first = list(buckets.values())[0].pathway_stats

            assert (
                first[
                    (
                        "direction:out,topic:arn:aws:sns:us-east-1:000000000000:testTopic,type:sns",
                        3337976778666780987,
                        0,
                    )
                ].full_pathway_latency.count
                >= 1
            )
            assert (
                first[
                    (
                        "direction:out,topic:arn:aws:sns:us-east-1:000000000000:testTopic,type:sns",
                        3337976778666780987,
                        0,
                    )
                ].edge_latency.count
                >= 1
            )
            assert (
                first[
                    (
                        "direction:out,topic:arn:aws:sns:us-east-1:000000000000:testTopic,type:sns",
                        3337976778666780987,
                        0,
                    )
                ].payload_size.count
                == 1
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 13854213076663332654, 3337976778666780987)
                ].full_pathway_latency.count
                >= 1
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 13854213076663332654, 3337976778666780987)
                ].edge_latency.count
                >= 1
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 13854213076663332654, 3337976778666780987)
                ].payload_size.count
                == 1
            )

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_data_streams_sqs(self):
        # DEV: We want to mock time to ensure we only create a single bucket
        with (
            mock.patch("time.time") as mt,
            mock.patch(
                "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
            ),
        ):
            mt.return_value = 1642544540

            pin = Pin(service=self.TEST_SERVICE)
            pin._tracer = self.tracer
            pin.onto(self.sqs_client)
            message_attributes = {
                "one": {"DataType": "String", "StringValue": "one"},
                "two": {"DataType": "String", "StringValue": "two"},
                "three": {"DataType": "String", "StringValue": "three"},
                "four": {"DataType": "String", "StringValue": "four"},
                "five": {"DataType": "String", "StringValue": "five"},
                "six": {"DataType": "String", "StringValue": "six"},
                "seven": {"DataType": "String", "StringValue": "seven"},
                "eight": {"DataType": "String", "StringValue": "eight"},
                "nine": {"DataType": "String", "StringValue": "nine"},
            }

            self.sqs_client.send_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world", MessageAttributes=message_attributes
            )

            self.sqs_client.receive_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"],
                MessageAttributeNames=["_datadog"],
                WaitTimeSeconds=2,
            )

            pin = Pin.get_from(self.sqs_client)
            buckets = pin.tracer.data_streams_processor._buckets
            assert len(buckets) == 1
            first = list(buckets.values())[0].pathway_stats

            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].full_pathway_latency.count >= 1
            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].edge_latency.count >= 1
            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].payload_size.count == 1
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 15625264005677082004, 15309751356108160802)
                ].full_pathway_latency.count
                >= 1
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 15625264005677082004, 15309751356108160802)
                ].edge_latency.count
                >= 1
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 15625264005677082004, 15309751356108160802)
                ].payload_size.count
                == 1
            )

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_data_streams_sqs_batch(self):
        # DEV: We want to mock time to ensure we only create a single bucket
        with (
            mock.patch("time.time") as mt,
            mock.patch(
                "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
            ),
        ):
            mt.return_value = 1642544540

            pin = Pin(service=self.TEST_SERVICE)
            pin._tracer = self.tracer
            pin.onto(self.sqs_client)
            message_attributes = {
                "one": {"DataType": "String", "StringValue": "one"},
                "two": {"DataType": "String", "StringValue": "two"},
                "three": {"DataType": "String", "StringValue": "three"},
                "four": {"DataType": "String", "StringValue": "four"},
                "five": {"DataType": "String", "StringValue": "five"},
                "six": {"DataType": "String", "StringValue": "six"},
                "seven": {"DataType": "String", "StringValue": "seven"},
                "eight": {"DataType": "String", "StringValue": "eight"},
                "nine": {"DataType": "String", "StringValue": "nine"},
            }

            entries = [
                {"Id": "1", "MessageBody": "Message No. 1", "MessageAttributes": message_attributes},
                {"Id": "2", "MessageBody": "Message No. 2", "MessageAttributes": message_attributes},
                {"Id": "3", "MessageBody": "Message No. 3", "MessageAttributes": message_attributes},
            ]

            self.sqs_client.send_message_batch(QueueUrl=self.sqs_test_queue["QueueUrl"], Entries=entries)

            self.sqs_client.receive_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"],
                MaxNumberOfMessages=3,
                MessageAttributeNames=["_datadog"],
                WaitTimeSeconds=2,
            )

            pin = Pin.get_from(self.sqs_client)
            buckets = pin.tracer.data_streams_processor._buckets
            assert len(buckets) == 1
            first = list(buckets.values())[0].pathway_stats

            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].full_pathway_latency.count >= 3
            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].edge_latency.count >= 3
            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].payload_size.count == 3
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 15625264005677082004, 15309751356108160802)
                ].full_pathway_latency.count
                >= 3
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 15625264005677082004, 15309751356108160802)
                ].edge_latency.count
                >= 3
            )
            assert (
                first[
                    ("direction:in,topic:Test,type:sqs", 15625264005677082004, 15309751356108160802)
                ].payload_size.count
                == 3
            )

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_data_streams_sqs_header_information(self):
        self.sqs_client.send_message(QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world")
        response = self.sqs_client.receive_message(
            QueueUrl=self.sqs_test_queue["QueueUrl"],
            MaxNumberOfMessages=1,
            WaitTimeSeconds=2,
            AttributeNames=[
                "All",
            ],
        )
        assert "_datadog" in response["Messages"][0]["MessageAttributes"]

    @mock_sqs
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    def test_data_streams_sqs_no_header(self):
        # DEV: We want to mock time to ensure we only create a single bucket
        with (
            mock.patch("time.time") as mt,
            mock.patch(
                "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
            ),
        ):
            mt.return_value = 1642544540

            pin = Pin(service=self.TEST_SERVICE)
            pin._tracer = self.tracer
            pin.onto(self.sqs_client)
            message_attributes = {
                "one": {"DataType": "String", "StringValue": "one"},
                "two": {"DataType": "String", "StringValue": "two"},
                "three": {"DataType": "String", "StringValue": "three"},
                "four": {"DataType": "String", "StringValue": "four"},
                "five": {"DataType": "String", "StringValue": "five"},
                "six": {"DataType": "String", "StringValue": "six"},
                "seven": {"DataType": "String", "StringValue": "seven"},
                "eight": {"DataType": "String", "StringValue": "eight"},
                "nine": {"DataType": "String", "StringValue": "nine"},
                "ten": {"DataType": "String", "StringValue": "ten"},
            }

            self.sqs_client.send_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"], MessageBody="world", MessageAttributes=message_attributes
            )

            self.sqs_client.receive_message(
                QueueUrl=self.sqs_test_queue["QueueUrl"],
                MessageAttributeNames=["_datadog"],
                WaitTimeSeconds=2,
            )

            pin = Pin.get_from(self.sqs_client)
            buckets = pin.tracer.data_streams_processor._buckets
            assert len(buckets) == 1
            first = list(buckets.values())[0].pathway_stats

            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].full_pathway_latency.count >= 1
            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].edge_latency.count >= 1
            assert first[("direction:out,topic:Test,type:sqs", 15309751356108160802, 0)].payload_size.count == 1
            assert first[("direction:in,topic:Test,type:sqs", 3569019635468821892, 0)].full_pathway_latency.count >= 1
            assert first[("direction:in,topic:Test,type:sqs", 3569019635468821892, 0)].edge_latency.count >= 1
            assert first[("direction:in,topic:Test,type:sqs", 3569019635468821892, 0)].payload_size.count == 1

    @mock_kinesis
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    @unittest.skipIf(BOTOCORE_VERSION < (1, 26, 31), "Kinesis didn't support streamARN till 1.26.31")
    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_kinesis_data_streams_enabled_put_records(self):
        with mock.patch(
            "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
        ):
            # (dict -> json string)[]
            data = json.dumps({"json": "string"})
            records = self._kinesis_generate_records(data, 2)
            client = self.session.create_client("kinesis", region_name="us-east-1")

            stream_name = "kinesis_put_records_data_streams"
            shard_id, stream_arn = self._kinesis_create_stream(client, stream_name)

            pin = Pin(service=self.TEST_SERVICE)
            pin._tracer = self.tracer
            pin.onto(client)
            client.put_records(StreamName=stream_name, Records=records, StreamARN=stream_arn)

            shard_iterator = self._kinesis_get_shard_iterator(client, stream_name, shard_id)
            client.get_records(ShardIterator=shard_iterator, StreamARN=stream_arn)

            pin = Pin.get_from(client)
            buckets = pin.tracer.data_streams_processor._buckets
            assert len(buckets) == 1
            first = list(buckets.values())[0].pathway_stats

            in_tags = ",".join(
                [
                    "direction:in",
                    "topic:arn:aws:kinesis:us-east-1:123456789012:stream/kinesis_put_records_data_streams",
                    "type:kinesis",
                ]
            )
            out_tags = ",".join(
                [
                    "direction:out",
                    "topic:arn:aws:kinesis:us-east-1:123456789012:stream/kinesis_put_records_data_streams",
                    "type:kinesis",
                ]
            )
            assert (
                first[
                    (
                        in_tags,
                        7250761453654470644,
                        17012262583645342129,
                    )
                ].full_pathway_latency.count
                >= 2
            )
            assert (
                first[
                    (
                        in_tags,
                        7250761453654470644,
                        17012262583645342129,
                    )
                ].edge_latency.count
                >= 2
            )
            assert (
                first[
                    (
                        in_tags,
                        7250761453654470644,
                        17012262583645342129,
                    )
                ].payload_size.count
                == 2
            )
            assert (
                first[
                    (
                        out_tags,
                        17012262583645342129,
                        0,
                    )
                ].full_pathway_latency.count
                >= 2
            )
            assert (
                first[
                    (
                        out_tags,
                        17012262583645342129,
                        0,
                    )
                ].edge_latency.count
                >= 2
            )
            assert (
                first[
                    (
                        out_tags,
                        17012262583645342129,
                        0,
                    )
                ].payload_size.count
                == 2
            )

    @mock_kinesis
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DATA_STREAMS_ENABLED="True"))
    @unittest.skipIf(BOTOCORE_VERSION < (1, 26, 31), "Kinesis didn't support streamARN till 1.26.31")
    @mock.patch("time.time", mock.MagicMock(return_value=1642544540))
    def test_kinesis_data_streams_enabled_put_record(self):
        # (dict -> json string)[]
        with mock.patch(
            "ddtrace.internal.datastreams.data_streams_processor", return_value=self.tracer.data_streams_processor
        ):
            data = json.dumps({"json": "string"})
            client = self.session.create_client("kinesis", region_name="us-east-1")

            stream_name = "kinesis_put_record_data_streams"
            shard_id, stream_arn = self._kinesis_create_stream(client, stream_name)

            partition_key = "1234"
            pin = Pin(service=self.TEST_SERVICE)
            pin._tracer = self.tracer
            pin.onto(client)
            client.put_record(StreamName=stream_name, Data=data, PartitionKey=partition_key, StreamARN=stream_arn)

            shard_iterator = self._kinesis_get_shard_iterator(client, stream_name, shard_id)
            client.get_records(ShardIterator=shard_iterator, StreamARN=stream_arn)

            pin = Pin.get_from(client)
            buckets = pin.tracer.data_streams_processor._buckets
            assert len(buckets) == 1
            first = list(buckets.values())[0].pathway_stats
            in_tags = ",".join(
                [
                    "direction:in",
                    "topic:arn:aws:kinesis:us-east-1:123456789012:stream/kinesis_put_record_data_streams",
                    "type:kinesis",
                ]
            )
            out_tags = ",".join(
                [
                    "direction:out",
                    "topic:arn:aws:kinesis:us-east-1:123456789012:stream/kinesis_put_record_data_streams",
                    "type:kinesis",
                ]
            )
            assert (
                first[
                    (
                        in_tags,
                        7186383338881463054,
                        14715769790627487616,
                    )
                ].full_pathway_latency.count
                >= 1
            )
            assert (
                first[
                    (
                        in_tags,
                        7186383338881463054,
                        14715769790627487616,
                    )
                ].edge_latency.count
                >= 1
            )
            assert (
                first[
                    (
                        in_tags,
                        7186383338881463054,
                        14715769790627487616,
                    )
                ].payload_size.count
                == 1
            )
            assert (
                first[
                    (
                        out_tags,
                        14715769790627487616,
                        0,
                    )
                ].full_pathway_latency.count
                >= 1
            )
            assert (
                first[
                    (
                        out_tags,
                        14715769790627487616,
                        0,
                    )
                ].edge_latency.count
                >= 1
            )
            assert (
                first[
                    (
                        out_tags,
                        14715769790627487616,
                        0,
                    )
                ].payload_size.count
                == 1
            )

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DATA_STREAMS_ENABLED="True",
            DD_BOTOCORE_DISTRIBUTED_TRACING="False",
            DD_BOTOCORE_PROPAGATION_ENABLED="False",
        )
    )
    @mock_kinesis
    def test_kinesis_put_records_inject_data_streams_to_every_record_propagation_disabled(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "kinesis_put_records_inject_data_streams_to_every_record_context_prop_disabled"
        shard_id, stream_arn = self._kinesis_create_stream(client, stream_name)

        data = json.dumps({"json": "string"})
        records = self._kinesis_generate_records(data, 5)

        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(client)
        client.put_records(StreamName=stream_name, Records=records, StreamARN=stream_arn)

        shard_iterator = self._kinesis_get_shard_iterator(client, stream_name, shard_id)

        response = client.get_records(ShardIterator=shard_iterator, StreamARN=stream_arn)

        for record in response["Records"]:
            data = json.loads(record["Data"])
            assert "_datadog" in data
            assert PROPAGATION_KEY_BASE_64 in data["_datadog"]

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DATA_STREAMS_ENABLED="True",
            DD_BOTOCORE_DISTRIBUTED_TRACING="True",
            DD_BOTOCORE_PROPAGATION_ENABLED="True",
        )
    )
    @mock_kinesis
    def test_kinesis_put_records_inject_data_streams_to_every_record_propagation_enabled(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "kinesis_put_records_inject_data_streams_to_every_record_prop_enabled"
        shard_id, stream_arn = self._kinesis_create_stream(client, stream_name)

        data = json.dumps({"json": "string"})
        records = self._kinesis_generate_records(data, 5)

        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(client)
        client.put_records(StreamName=stream_name, Records=records, StreamARN=stream_arn)

        shard_iterator = self._kinesis_get_shard_iterator(client, stream_name, shard_id)

        response = client.get_records(ShardIterator=shard_iterator, StreamARN=stream_arn)

        for record in response["Records"]:
            data = json.loads(record["Data"])
            assert "_datadog" in data
            assert PROPAGATION_KEY_BASE_64 in data["_datadog"]

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DATA_STREAMS_ENABLED="False",
            DD_BOTOCORE_DISTRIBUTED_TRACING="False",
            DD_BOTOCORE_PROPAGATION_ENABLED="False",
        )
    )
    @mock_kinesis
    def test_kinesis_put_records_inject_data_streams_to_every_record_disable_all_injection(self):
        client = self.session.create_client("kinesis", region_name="us-east-1")

        stream_name = "kinesis_put_records_inject_data_streams_to_every_record_all_injection_disabled"
        shard_id, stream_arn = self._kinesis_create_stream(client, stream_name)

        data = json.dumps({"json": "string"})
        records = self._kinesis_generate_records(data, 5)

        pin = Pin(service=self.TEST_SERVICE)
        pin._tracer = self.tracer
        pin.onto(client)
        client.put_records(StreamName=stream_name, Records=records, StreamARN=stream_arn)

        shard_iterator = self._kinesis_get_shard_iterator(client, stream_name, shard_id)

        response = client.get_records(ShardIterator=shard_iterator, StreamARN=stream_arn)

        for record in response["Records"]:
            data = json.loads(record["Data"])
            assert "_datadog" not in data
