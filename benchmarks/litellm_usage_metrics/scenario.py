import bm


class LiteLLMUsageMetrics(bm.Scenario):
    tagged: bool

    def run(self):
        from ddtrace.contrib.internal.litellm._usage_metrics import GATEWAY_REQUEST
        from ddtrace.contrib.internal.litellm._usage_metrics import PROVIDER_ATTEMPT
        from ddtrace.contrib.internal.litellm._usage_metrics import TOKEN_BREAKDOWN
        from ddtrace.contrib.internal.litellm._usage_metrics_writer import UsageMetricsWriter

        writer = UsageMetricsWriter("otlp", interval=60)
        attempt = {
            "operation_name": "chat",
            "provider_name": "openai",
            "request_model": "gpt-4o-mini",
            "response_model": "gpt-4o-mini-2024-07-18",
            "duration_seconds": 1.5,
            "time_to_first_chunk_seconds": 0.2,
            "streaming": True,
            "input_tokens": 120,
            "input_basis": "includes_cache",
            "output_tokens": 48,
            "output_basis": "includes_reasoning",
            "cache_read_input_tokens": 64,
            "reasoning_output_tokens": 16,
            "cost_usd": 5.7e-6,
            "cost_source": "estimated",
            "observation_point": "gateway",
        }
        gateway = {
            "operation_name": "chat",
            "duration_seconds": 1.6,
            "observation_point": "gateway",
            "provider_operations": 1,
            "provider_operation_coverage": "complete",
            "request_model": "gpt",
            "estimated_cost_usd": 5.7e-6,
        }
        attributes = (
            {
                "user.id": "user-alice",
                "trajectory.team.id": "team-ml",
                "trajectory.gateway.key.alias": "alice-key",
                "trajectory.gateway.route": "gpt",
                "trajectory.gateway.destination.id": "deployment-1",
            }
            if self.tagged
            else None
        )

        def _(loops):
            # What one proxied call records: its provider attempt, its token breakdown and its gateway request.
            for _ in range(loops):
                writer.record(PROVIDER_ATTEMPT, attempt, attributes)
                writer.record(TOKEN_BREAKDOWN, attempt, attributes)
                writer.record(GATEWAY_REQUEST, gateway, attributes)

        yield _
