"""Full OpenFeature client/provider evaluation, with bounded in-process telemetry."""

import os
import queue
from unittest.mock import patch

import bm


class DiscardingQueue(queue.Queue):
    def __init__(self, maxsize):
        super().__init__(maxsize=maxsize)
        self.enqueued = 0

    def _put(self, item):
        self.enqueued += 1
        self.queue.append(item)
        self.queue.popleft()


class OpenFeatureOptOut(bm.Scenario):
    enabled: bool
    opted_out: bool

    def run(self):
        from openfeature import api
        from openfeature.evaluation_context import EvaluationContext

        from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
        from ddtrace.internal.openfeature._native import process_ffe_configuration
        from ddtrace.openfeature import DataDogProvider

        configuration = {
            "id": "benchmark",
            "createdAt": "2025-10-31T00:00:00Z",
            "format": "SERVER",
            "environment": {"name": "benchmark"},
            "flags": {
                "flag": {
                    "key": "flag",
                    "enabled": True,
                    "variationType": "BOOLEAN",
                    "variations": {"on": {"key": "on", "value": True}},
                    "allocations": [
                        {"key": "allocation", "splits": [{"variationKey": "on", "shards": []}], "doLog": False}
                    ],
                }
            },
        }
        assert process_ffe_configuration(configuration)
        with (
            patch.dict(os.environ, {"DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED": "true"}),
            patch.object(DataDogProvider, "_start_configuration_source", lambda self: None),
            patch.object(FlagEvaluationWriter, "start", lambda self: None),
            patch("ddtrace.internal.openfeature._provider.start_exposure_writer", lambda: None),
        ):
            provider = DataDogProvider(enable_evaluation_telemetry_opt_out_parameter=self.enabled)
            api.set_provider_and_wait(provider, "benchmark")
            client = api.get_client("benchmark")
            writer = provider._flag_eval_evp_writer
            assert writer is not None
            writer._queue = DiscardingQueue(maxsize=writer._queue.maxsize)
            subject = EvaluationContext(
                targeting_key="benchmark", attributes={"dd_evaluation_telemetry_opt_out": self.opted_out}
            )
            result = client.get_boolean_details("flag", False, subject)
            assert result.value is True and result.error_code is None
            assert writer._queue.enqueued == int(not (self.enabled and self.opted_out))

            def evaluate(loops):
                for _ in range(loops):
                    client.get_boolean_value("flag", False, subject)

            try:
                yield evaluate
            finally:
                api.shutdown()
