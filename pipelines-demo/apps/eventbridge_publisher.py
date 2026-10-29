"""eb-publisher: PutEvents with DETAIL_TYPE to the event bus (pipeline source).

The tracer records the produce side as ``eventbridge``/<detail-type>; the bus
name is not part of the side key (see PIPELINE_TOPOLOGIES.md section 5).
"""

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common


bus = os.environ.get("BUS", "default")
detail_type = os.environ.get("DETAIL_TYPE", "demo-order")
count = int(os.environ.get("MSGS", "3"))

events = common.boto3_client("events")
for i in range(count):
    events.put_events(
        Entries=[
            {
                "EventBusName": bus,
                "Source": "demo.src",
                "DetailType": detail_type,
                "Detail": '{"i": %d}' % i,
            }
        ]
    )
print("eb-publisher put %d events with detail-type %r on bus %s" % (count, detail_type, bus))
