#!/bin/python
import json
import sys

from datadog import initialize
from datadog import statsd


options = {"statsd_host": "0.0.0.0", "statsd_port": 8125}

initialize(**options)

if __name__ == "__main__":
    payload = sys.stdin.read()
    payload = json.loads(payload)
    for m in payload["series"]:
        tags = m["tags"] or {}
        tags.update(
            {
                "lib_language": payload["lib_language"],
                "lib_version": payload["lib_version"],
            }
        )
        statsd.increment(m["metric"], tags=["%s:%s" % (k, v) for k, v in tags.items()])
