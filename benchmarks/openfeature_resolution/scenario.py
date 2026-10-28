"""Measure the native binding's independent-flag path as dependency support grows."""

import json

import bm


class OpenFeatureResolution(bm.Scenario):
    targeting: bool

    def run(self):
        from ddtrace.internal.native._native import ffe

        allocation = {"key": "targeting", "splits": [{"variationKey": "on", "shards": []}], "doLog": False}
        if self.targeting:
            allocation["rules"] = [{"conditions": [{"attribute": "plan", "operator": "ONE_OF", "value": ["pro"]}]}]
        config = ffe.Configuration(
            json.dumps(
                {
                    "createdAt": "2026-01-01T00:00:00Z",
                    "environment": {"name": "benchmark"},
                    "flags": {
                        "flag": {
                            "key": "flag",
                            "enabled": True,
                            "variationType": "BOOLEAN",
                            "variations": {"on": {"key": "on", "value": True}},
                            "allocations": [allocation],
                        }
                    },
                }
            ).encode()
        )
        context = {"targeting_key": "subject", "attributes": {"plan": "pro"}}
        resolve = config.resolve_value
        flag_type = ffe.FlagType.Boolean
        assert resolve("flag", flag_type, context).value is True

        def _(loops):
            for _ in range(loops):
                resolve("flag", flag_type, context)

        yield _
