"""Create the Kinesis streams used by the demo topologies.

Run with plain python3 (NOT ddtrace-run) so no sides are recorded:
    python3 setup/setup_kinesis.py
"""

import sys
import time


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


STREAMS = ["kin-a", "kin-b"]

kinesis = common.boto3_client("kinesis")
for stream in STREAMS:
    try:
        kinesis.create_stream(StreamName=stream, ShardCount=1)
    except kinesis.exceptions.ResourceInUseException:  # already exists
        pass
    deadline = time.time() + 60
    while time.time() < deadline:
        status = kinesis.describe_stream(StreamName=stream)["StreamDescription"]["StreamStatus"]
        if status == "ACTIVE":
            print("kinesis stream ACTIVE: %s" % stream)
            break
        time.sleep(1)
