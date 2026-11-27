import botocore.session
import pytest


def _kinesis_get_records_accepts_stream_arn():
    model = botocore.session.get_session().get_service_model("kinesis")
    return "StreamARN" in model.operation_model("GetRecords").input_shape.members


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "true"}, err=None)
def test_data_streams_sqs_send_and_receive():
    import asyncio
    import json

    from ddtrace.contrib.internal.aiobotocore.patch import patch
    from ddtrace.internal.datastreams import data_streams_processor
    from tests.contrib.aiobotocore.utils import aiobotocore_client
    from tests.datastreams.utils import pathway_payload_counts

    patch()
    queue_name = "aiobotocore-dsm-sqs"

    async def run():
        async with aiobotocore_client("sqs", None) as sqs:
            queue_url = (await sqs.create_queue(QueueName=queue_name))["QueueUrl"]
            await sqs.send_message(QueueUrl=queue_url, MessageBody="one")
            await sqs.send_message_batch(
                QueueUrl=queue_url,
                Entries=[{"Id": "2", "MessageBody": "two"}, {"Id": "3", "MessageBody": "three"}],
            )
            messages = []
            while len(messages) < 3:
                response = await sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1)
                messages.extend(response.get("Messages", []))
            await sqs.delete_queue(QueueUrl=queue_url)
            return messages

    messages = asyncio.run(run())

    assert len(messages) == 3
    for message in messages:
        assert "dd-pathway-ctx-base64" in json.loads(message["MessageAttributes"]["_datadog"]["StringValue"])

    counts = pathway_payload_counts(data_streams_processor())
    out_entries = [
        (h, p, c) for (tags, h, p), c in counts.items() if tags == "direction:out,topic:%s,type:sqs" % queue_name
    ]
    in_entries = [
        (h, p, c) for (tags, h, p), c in counts.items() if tags == "direction:in,topic:%s,type:sqs" % queue_name
    ]
    assert len(out_entries) == 1, counts
    assert len(in_entries) == 1, counts
    out_hash, out_parent, out_count = out_entries[0]
    _, in_parent, in_count = in_entries[0]
    assert out_parent == 0
    assert in_parent == out_hash
    assert out_count == 3
    assert in_count == 3


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "true"}, err=None)
def test_data_streams_concurrent_tasks_keep_their_own_pathway():
    import asyncio

    from ddtrace.contrib.internal.aiobotocore.patch import patch
    from ddtrace.internal.datastreams import data_streams_processor
    from tests.contrib.aiobotocore.utils import aiobotocore_client
    from tests.datastreams.utils import pathway_stats_merged

    patch()

    async def run():
        async with aiobotocore_client("sqs", None) as sqs:
            urls = {}
            for name in ("in-a", "in-b", "out-a", "out-b"):
                urls[name] = (await sqs.create_queue(QueueName="aiobotocore-dsm-tasks-" + name))["QueueUrl"]
            await sqs.send_message(QueueUrl=urls["in-a"], MessageBody="a")
            await sqs.send_message(QueueUrl=urls["in-b"], MessageBody="b")
            a_received = asyncio.Event()
            b_received = asyncio.Event()

            async def worker(source, target, received, wait_for):
                messages = []
                while not messages:
                    response = await sqs.receive_message(QueueUrl=urls[source], WaitTimeSeconds=1)
                    messages = response.get("Messages", [])
                received.set()
                await wait_for.wait()
                await sqs.send_message(QueueUrl=urls[target], MessageBody=messages[0]["Body"])

            await asyncio.gather(
                worker("in-a", "out-a", a_received, b_received),
                worker("in-b", "out-b", b_received, a_received),
            )
            for url in urls.values():
                await sqs.delete_queue(QueueUrl=url)

    asyncio.run(run())

    stats = pathway_stats_merged(data_streams_processor())

    def entry(tags):
        entries = [(h, p) for (t, h, p) in stats if t == tags]
        assert len(entries) == 1, (tags, stats)
        return entries[0]

    for name in ("a", "b"):
        in_hash, _ = entry("direction:in,topic:aiobotocore-dsm-tasks-in-%s,type:sqs" % name)
        _, out_parent = entry("direction:out,topic:aiobotocore-dsm-tasks-out-%s,type:sqs" % name)
        assert out_parent == in_hash, name


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "true"}, err=None)
def test_data_streams_sns_to_sqs():
    import asyncio

    from ddtrace.contrib.internal.aiobotocore.patch import patch
    from ddtrace.internal.datastreams import data_streams_processor
    from tests.contrib.aiobotocore.utils import aiobotocore_client
    from tests.datastreams.utils import pathway_stats_merged

    patch()
    queue_name = "aiobotocore-dsm-sns-queue"

    async def run():
        async with aiobotocore_client("sns", None) as sns, aiobotocore_client("sqs", None) as sqs:
            topic_arn = (await sns.create_topic(Name="aiobotocore-dsm-topic"))["TopicArn"]
            queue_url = (await sqs.create_queue(QueueName=queue_name))["QueueUrl"]
            queue_arn = (await sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"]))["Attributes"][
                "QueueArn"
            ]
            await sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)
            await sns.publish(TopicArn=topic_arn, Message="hello")
            messages = []
            while not messages:
                response = await sqs.receive_message(QueueUrl=queue_url, WaitTimeSeconds=1)
                messages = response.get("Messages", [])
            await sqs.delete_queue(QueueUrl=queue_url)
            await sns.delete_topic(TopicArn=topic_arn)
            return topic_arn

    topic_arn = asyncio.run(run())

    stats = pathway_stats_merged(data_streams_processor())
    out_entries = [(h, p) for (tags, h, p) in stats if tags == "direction:out,topic:%s,type:sns" % topic_arn]
    in_entries = [(h, p) for (tags, h, p) in stats if tags == "direction:in,topic:%s,type:sqs" % queue_name]
    assert len(out_entries) == 1, stats
    assert len(in_entries) == 1, stats
    assert in_entries[0][1] == out_entries[0][0]


@pytest.mark.skipif(
    not _kinesis_get_records_accepts_stream_arn(),
    reason="GetRecords names no stream before botocore added StreamARN, so no consume checkpoint can be set",
)
@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "true"}, err=None)
def test_data_streams_kinesis_put_and_get():
    import asyncio
    import json

    from ddtrace.contrib.internal.aiobotocore.patch import patch
    from ddtrace.internal.datastreams import data_streams_processor
    from tests.contrib.aiobotocore.utils import aiobotocore_client
    from tests.datastreams.utils import pathway_payload_counts

    patch()
    stream_name = "aiobotocore-dsm-stream"

    async def run():
        async with aiobotocore_client("kinesis", None) as kinesis:
            await kinesis.create_stream(StreamName=stream_name, ShardCount=1)
            description = (await kinesis.describe_stream(StreamName=stream_name))["StreamDescription"]
            stream_arn = description["StreamARN"]
            shard_id = description["Shards"][0]["ShardId"]
            await kinesis.put_record(StreamARN=stream_arn, Data=json.dumps({"id": 1}), PartitionKey="1")
            await kinesis.put_records(
                StreamARN=stream_arn,
                Records=[{"Data": json.dumps({"id": i}), "PartitionKey": str(i)} for i in (2, 3)],
            )
            iterator = (
                await kinesis.get_shard_iterator(
                    StreamARN=stream_arn, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON"
                )
            )["ShardIterator"]
            records = (await kinesis.get_records(StreamARN=stream_arn, ShardIterator=iterator))["Records"]
            await kinesis.delete_stream(StreamARN=stream_arn)
            return stream_arn, records

    stream_arn, records = asyncio.run(run())

    assert len(records) == 3
    for record in records:
        assert "dd-pathway-ctx-base64" in json.loads(record["Data"])["_datadog"]

    counts = pathway_payload_counts(data_streams_processor())
    assert {tags for (tags, _, _) in counts} == {
        "direction:out,topic:%s,type:kinesis" % stream_arn,
        "direction:in,topic:%s,type:kinesis" % stream_arn,
    }, counts
    out_entries = [(h, p, c) for (tags, h, p), c in counts.items() if tags.startswith("direction:out,")]
    in_entries = [(h, p, c) for (tags, h, p), c in counts.items() if tags.startswith("direction:in,")]
    assert len(out_entries) == 1, counts
    assert len(in_entries) == 1, counts
    assert out_entries[0][2] == 3
    assert in_entries[0][1] == out_entries[0][0]
    assert in_entries[0][2] == 3


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "true"}, err=b"")
def test_data_streams_kinesis_get_records_without_stream_sets_no_consume_checkpoint():
    import asyncio
    import json

    from ddtrace.contrib.internal.aiobotocore.patch import patch
    from ddtrace.internal.datastreams import data_streams_processor
    from tests.contrib.aiobotocore.utils import aiobotocore_client
    from tests.datastreams.utils import pathway_stats_merged

    patch()
    stream_name = "aiobotocore-dsm-no-stream"

    async def run():
        async with aiobotocore_client("kinesis", None) as kinesis:
            await kinesis.create_stream(StreamName=stream_name, ShardCount=1)
            shard_id = (await kinesis.describe_stream(StreamName=stream_name))["StreamDescription"]["Shards"][0][
                "ShardId"
            ]
            await kinesis.put_record(StreamName=stream_name, Data=json.dumps({"id": 1}), PartitionKey="1")
            iterator = (
                await kinesis.get_shard_iterator(
                    StreamName=stream_name, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON"
                )
            )["ShardIterator"]
            records = (await kinesis.get_records(ShardIterator=iterator))["Records"]
            await kinesis.delete_stream(StreamName=stream_name)
            return records

    records = asyncio.run(run())

    assert len(records) == 1
    tags = {tags for (tags, _, _) in pathway_stats_merged(data_streams_processor())}
    assert tags == {"direction:out,topic:%s,type:kinesis" % stream_name}, tags


@pytest.mark.subprocess(err=None)
def test_data_streams_disabled_leaves_messages_unchanged():
    import asyncio
    import json

    from ddtrace.contrib.internal.aiobotocore.patch import patch
    from ddtrace.internal.datastreams import data_streams_processor
    from tests.contrib.aiobotocore.utils import aiobotocore_client

    patch()

    async def run():
        async with aiobotocore_client("sqs", None) as sqs, aiobotocore_client("kinesis", None) as kinesis:
            queue_url = (await sqs.create_queue(QueueName="aiobotocore-dsm-disabled"))["QueueUrl"]
            await sqs.send_message(QueueUrl=queue_url, MessageBody="one")
            response = await sqs.receive_message(QueueUrl=queue_url, MessageAttributeNames=["All"], WaitTimeSeconds=1)
            await sqs.delete_queue(QueueUrl=queue_url)

            await kinesis.create_stream(StreamName="aiobotocore-dsm-disabled", ShardCount=1)
            shard_id = (await kinesis.describe_stream(StreamName="aiobotocore-dsm-disabled"))["StreamDescription"][
                "Shards"
            ][0]["ShardId"]
            await kinesis.put_record(
                StreamName="aiobotocore-dsm-disabled", Data=json.dumps({"id": 1}), PartitionKey="1"
            )
            iterator = (
                await kinesis.get_shard_iterator(
                    StreamName="aiobotocore-dsm-disabled", ShardId=shard_id, ShardIteratorType="TRIM_HORIZON"
                )
            )["ShardIterator"]
            records = (await kinesis.get_records(ShardIterator=iterator))["Records"]
            await kinesis.delete_stream(StreamName="aiobotocore-dsm-disabled")
            return response["Messages"], records

    messages, records = asyncio.run(run())

    assert data_streams_processor() is None
    assert "MessageAttributes" not in messages[0]
    assert json.loads(records[0]["Data"]) == {"id": 1}
