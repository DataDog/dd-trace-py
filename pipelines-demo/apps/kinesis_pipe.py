"""kinesis-pipe: consumes STREAM_IN and produces to STREAM_OUT (in-process link)."""

import os
import sys
import time


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


stream_in = os.environ.get("STREAM_IN", "kin-a")
stream_out = os.environ.get("STREAM_OUT", "kin-b")
target = int(os.environ.get("MSGS", "3"))

kinesis = common.boto3_client("kinesis")
shard_id = kinesis.describe_stream(StreamName=stream_in)["StreamDescription"]["Shards"][0]["ShardId"]
iterator = kinesis.get_shard_iterator(StreamName=stream_in, ShardId=shard_id, ShardIteratorType="TRIM_HORIZON")[
    "ShardIterator"
]

piped = 0
deadline = time.time() + 60
while piped < target and time.time() < deadline:
    response = kinesis.get_records(ShardIterator=iterator, Limit=10)
    iterator = response.get("NextShardIterator", iterator)
    for record in response.get("Records", []):
        kinesis.put_record(StreamName=stream_out, Data=record["Data"], PartitionKey="pipe")
        piped += 1
    if not response.get("Records"):
        time.sleep(0.5)
print("kinesis-pipe piped %d records %s -> %s" % (piped, stream_in, stream_out))
