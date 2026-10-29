"""kinesis-src: puts demo records into a Kinesis stream (pipeline source)."""

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


stream = os.environ.get("STREAM", "kin-a")
count = int(os.environ.get("MSGS", "3"))

kinesis = common.boto3_client("kinesis")
for i in range(count):
    kinesis.put_record(StreamName=stream, Data="msg-%d" % i, PartitionKey="src")
print("kinesis-src put %d records into %s" % (count, stream))
