"""kinesis-sink: consumes a Kinesis stream (pipeline end)."""

import os
import sys
import time


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


stream = os.environ.get("STREAM", "kin-b")
target = int(os.environ.get("MSGS", "3"))

kinesis = common.boto3_client("kinesis")
shard_id = kinesis.describe_stream(StreamName=stream)["StreamDescription"]["Shards"][0]["ShardId"]
iterator = kinesis.get_shard_iterator(StreamName=stream, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")[
    "ShardIterator"
]

received = 0
deadline = time.time() + 60
while received < target and time.time() < deadline:
    response = kinesis.get_records(ShardIterator=iterator, Limit=10)
    iterator = response.get("NextShardIterator", iterator)
    for _ in response.get("Records", []):
        received += 1
    if not response.get("Records"):
        time.sleep(0.5)
print("kinesis-sink received %d records from %s" % (received, stream))
