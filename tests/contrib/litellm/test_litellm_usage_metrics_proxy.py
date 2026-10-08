"""End to end: a real LiteLLM proxy under ddtrace-run, with usage metrics enabled, in front of a fake provider.

The proxy runs in a subprocess, as in a deployment. Every scenario's metrics are checked in the OTLP export, and
the same scenarios exported over DogStatsD must give the same series.
"""

import collections
import hashlib
import importlib.metadata
import re
import time

import pytest

from ddtrace.contrib.internal.litellm._usage_metrics import ATTEMPT_PROFILES
from ddtrace.contrib.internal.litellm._usage_metrics import GATEWAY_PROFILES
from ddtrace.contrib.internal.litellm._usage_metrics import GATEWAY_REQUEST
from ddtrace.contrib.internal.litellm._usage_metrics import PROVIDER_ATTEMPT
from ddtrace.contrib.internal.litellm._usage_metrics import RECORDED_PROFILES_TAG
from ddtrace.contrib.internal.litellm._usage_metrics import TOKEN_BREAKDOWN
from ddtrace.contrib.internal.litellm._usage_metrics_writer import ai_usage
from tests.contrib.litellm.usage_metrics_proxy import KEYS
from tests.contrib.litellm.usage_metrics_proxy import PROVIDER_KEYS
from tests.contrib.litellm.usage_metrics_proxy import Gateway


pytest.importorskip("litellm.proxy.proxy_server", reason="the LiteLLM proxy extras are not installed")
metrics_service_pb2 = pytest.importorskip("opentelemetry.proto.collector.metrics.v1.metrics_service_pb2")
pytestmark = pytest.mark.skipif(ai_usage is None, reason="native ai_usage module not built")

LITELLM_VERSION = tuple(int(part) for part in importlib.metadata.version("litellm").split(".")[:2])
ALICE = {"user.id": "user-alice", "trajectory.team.id": "team-ml", "trajectory.gateway.key.alias": "alice-key"}


def run_scenarios(proxy):
    """Each scenario, and the HTTP status the client must get."""
    assert proxy.chat("gpt-4o-mini")[0] == 200
    assert proxy.chat("gpt-4o-mini", stream=True, stream_options={"include_usage": True})[0] == 200
    assert proxy.chat("gpt-4o-mini-nousage", stream=True)[0] == 200
    assert proxy.chat("flaky-gpt")[0] == 200
    assert proxy.chat("primary-fails")[0] == 200
    assert proxy.chat("always-fails")[0] >= 500
    assert proxy.request("/v1/embeddings", {"model": "text-embedding-3-small", "input": "hi"})[0] == 200
    messages = {
        "model": "claude-haiku-4-5",
        "max_tokens": 16,
        "stream": True,
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert proxy.request("/v1/messages", messages)[0] == 200
    for _ in range(2):
        assert proxy.chat("gpt-4o-mini", key="sk-bob-0002", cache={"use-cache": True})[0] == 200
    assert proxy.chat("custom-unpriced")[0] == 200
    # LiteLLM delivers its logging callbacks after the response; let them run and the writer flush (every 0.5 s)
    # before the proxy is stopped.
    time.sleep(2)


class Export:
    """The series of an OTLP export: counter sums and histogram sample counts per profile, name and attributes."""

    def __init__(self, bodies):
        self.counters = collections.Counter()
        self.samples = collections.Counter()
        self.scopes = set()
        for body in bodies:
            request = metrics_service_pb2.ExportMetricsServiceRequest()
            request.ParseFromString(body)
            for resource_metrics in request.resource_metrics:
                for scope_metrics in resource_metrics.scope_metrics:
                    profile = {a.key: a.value.string_value for a in scope_metrics.scope.attributes}[
                        "trajectory.profile"
                    ]
                    self.scopes.add((scope_metrics.scope.name, profile))
                    for metric in scope_metrics.metrics:
                        kind = metric.WhichOneof("data")
                        for point in getattr(metric, kind).data_points:
                            attributes = tuple(sorted((a.key, a.value.string_value) for a in point.attributes))
                            key = (profile, metric.name, metric.unit, attributes)
                            if kind == "sum":
                                self.counters[key] += point.as_int if point.HasField("as_int") else point.as_double
                            else:
                                self.samples[key] += point.count

    @staticmethod
    def _matches(attributes, wanted):
        """``None`` wants the attribute absent and ``"*"`` wants it present with any value."""
        attributes = dict(attributes)

        def match(key, value):
            if value is None:
                return key not in attributes
            if value == "*":
                return key in attributes
            return attributes.get(key) == value

        return all(match(k, v) for k, v in wanted.items())

    def total(self, name, **wanted):
        wanted = {k.replace("__", "."): v for k, v in wanted.items()}
        return sum(v for (_, n, _, a), v in self.counters.items() if n == name and self._matches(a, wanted))

    def count(self, name, **wanted):
        wanted = {k.replace("__", "."): v for k, v in wanted.items()}
        return sum(v for (_, n, _, a), v in self.samples.items() if n == name and self._matches(a, wanted))

    def series(self, name, **wanted):
        wanted = {k.replace("__", "."): v for k, v in wanted.items()}
        return [
            dict(a)
            for (_, n, _, a) in list(self.counters) + list(self.samples)
            if n == name and self._matches(a, wanted)
        ]

    def datadog_series(self):
        """The Datadog series these points give: Counts for counters (nano-USD in USD) and each histogram's
        ``.count`` companion. Durations differ between runs; their sample counts do not.
        """
        out = collections.Counter()
        for (profile, name, unit, attributes), value in self.counters.items():
            out[(name, _tags(profile, attributes))] += value / 1e9 if unit == "{nanoUSD}" else value
        for (profile, name, _, attributes), value in self.samples.items():
            out[(name + ".count", _tags(profile, attributes))] += value
        return out


def _tags(profile, attributes):
    def normalize(value):
        return re.sub(r"[^a-z0-9_\-:./]", "_", value.lower())

    tags = [f"{k}:{normalize(v)}" for k, v in attributes]
    tags.append("trajectory.profile:" + profile.replace("@", "/"))
    return tuple(sorted(tags))


def dogstatsd_series(lines):
    out = collections.Counter()
    for line in lines:
        head, kind, tags = (line.split("|") + [""])[:3]
        name, value = head.rsplit(":", 1)
        if kind == "c" and not name.endswith(".sum"):
            out[(name, tuple(sorted(tags[1:].split(","))))] += float(value)
    return out


@pytest.fixture
def gateway():
    gateway = Gateway()
    yield gateway
    gateway.close()


def test_proxy_metrics_over_otlp_and_dogstatsd(gateway, tmp_path):
    otlp_dir, dogstatsd_dir = tmp_path / "otlp", tmp_path / "dogstatsd"
    otlp_dir.mkdir()
    dogstatsd_dir.mkdir()

    proxy = gateway.proxy(otlp_dir, "otlp")
    try:
        run_scenarios(proxy)
    finally:
        proxy.stop()
    log = proxy.output()
    bodies = list(gateway.otlp_bodies)
    assert bodies, f"no OTLP export received:\n{log}"
    export = Export(bodies)

    # One scope per profile, named after the integration.
    assert export.scopes == {
        ("ddtrace.contrib.litellm", PROVIDER_ATTEMPT),
        ("ddtrace.contrib.litellm", TOKEN_BREAKDOWN),
        ("ddtrace.contrib.litellm", GATEWAY_REQUEST),
    }

    # A plain call and a stream with provider usage: input includes cached input; nothing is estimated.
    gpt = dict(ALICE, gen_ai__request__model="gpt-4o-mini", trajectory__gateway__route="gpt-4o-mini")
    assert export.count("gen_ai.client.inference.duration", error__type=None, **gpt) == 2
    assert export.total("gen_ai.client.inference.usage.input_tokens", trajectory__token__source=None, **gpt) == 24
    assert export.total("gen_ai.client.inference.usage.output_tokens", trajectory__token__source=None, **gpt) == 14
    assert export.total("gen_ai.client.inference.usage.cache_read.input_tokens", **gpt) == 8
    assert (
        export.total("trajectory.gen_ai.client.inference.usage.cost", trajectory__cost__source="estimated", **gpt) > 0
    )
    assert {s.get("gen_ai.response.model") for s in export.series("gen_ai.client.inference.duration", **gpt)} >= {
        "gpt-4o-mini-2024-07-18"
    }
    assert all(s["gen_ai.provider.name"] == "openai" for s in export.series("gen_ai.client.inference.duration", **gpt))
    assert all(
        s["trajectory.observation.point"] == "gateway" for s in export.series("gen_ai.client.inference.duration")
    )

    # A stream whose provider sent no usage: LiteLLM counted the tokens, and the points say so.
    nousage = {"trajectory.gateway.route": "gpt-4o-mini-nousage"}
    assert (
        export.total("gen_ai.client.inference.usage.input_tokens", trajectory__token__source="estimated", **nousage) > 0
    )
    assert export.total("gen_ai.client.inference.usage.input_tokens", trajectory__token__source=None, **nousage) == 0

    # Provider attempts are compared with what the provider received: how many times LiteLLM retries depends on
    # its version.
    sent = collections.Counter(model for _, model, _ in gateway.provider_requests)

    # A retry: every rate-limited attempt and the success, and one charge.
    flaky = {"trajectory.gateway.route": "flaky-gpt"}
    assert sent["gpt-4o-mini-flaky"] >= 2
    assert export.count("gen_ai.client.inference.duration", error__type="rate_limit_error", **flaky) == (
        sent["gpt-4o-mini-flaky"] - 1
    )
    assert export.count("gen_ai.client.inference.duration", error__type=None, **flaky) == 1
    assert export.total("gen_ai.client.inference.usage.input_tokens", **flaky) == 12

    # A fallback: the failed attempts and the fallback all carry the client's route.
    fallback = {"trajectory.gateway.route": "primary-fails"}
    assert (
        export.count("gen_ai.client.inference.duration", error__type="*", **fallback)
        == (sent["gpt-4o-mini-fail500-primary"])
    )
    assert export.count("gen_ai.client.inference.duration", error__type=None, **fallback) == 1
    destinations = {
        s.get("trajectory.gateway.destination.id")
        for s in export.series("gen_ai.client.inference.duration", **fallback)
    }
    assert len(destinations) == 2

    # A request that fails for good: every attempt is recorded as failed, with no usage and no cost.
    failed = {"trajectory.gateway.route": "always-fails"}
    assert sent["gpt-4o-mini-fail500-always"] >= 1
    assert (
        export.count("gen_ai.client.inference.duration", error__type="*", **failed)
        == (sent["gpt-4o-mini-fail500-always"])
    )
    assert export.total("gen_ai.client.inference.usage.input_tokens", **failed) == 0
    assert export.total("trajectory.gen_ai.gateway.request.estimated_cost", **failed) == 0
    assert export.series("trajectory.gen_ai.gateway.request.estimated_cost", **failed) == []

    # Embeddings record the operation duration and input tokens only.
    embeddings = {"gen_ai.operation.name": "embeddings"}
    assert export.count("gen_ai.client.operation.duration", **embeddings) == 1
    assert export.total("gen_ai.client.inference.usage.input_tokens", **embeddings) == 3
    assert export.series("gen_ai.client.inference.usage.output_tokens", **embeddings) == []

    # The Anthropic Messages route: input includes cache reads and writes, and writes are split by lifetime.
    # LiteLLM before 1.80 serves /v1/messages as a pass-through whose own logging records no usage.
    if LITELLM_VERSION >= (1, 80):
        claude = {"gen_ai.provider.name": "anthropic", "trajectory.token.source": None}
        assert export.total("gen_ai.client.inference.usage.input_tokens", **claude) == 60
        assert export.total("gen_ai.client.inference.usage.output_tokens", **claude) == 5
        assert export.total("gen_ai.client.inference.usage.cache_read.input_tokens", **claude) == 20
        assert export.total("gen_ai.client.inference.usage.cache_write.input_tokens", **claude) == 30
        lifetimes = {
            s["trajectory.cache.lifetime"]: export.total(
                "gen_ai.client.inference.usage.cache_write.input_tokens",
                trajectory__cache__lifetime=s["trajectory.cache.lifetime"],
                **claude,
            )
            for s in export.series("gen_ai.client.inference.usage.cache_write.input_tokens", **claude)
        }
        # LiteLLM versions that keep Anthropic's split of cache writes by lifetime report it; others report the total.
        assert lifetimes in ({"5m": 18, "1h": 12}, {"unspecified": 30}), lifetimes

    # A gateway cache hit: one provider attempt for two requests, and a charge of exactly zero for the hit.
    bob = {"user.id": "user-bob"}
    assert export.count("gen_ai.client.inference.duration", **bob) == 1
    assert [p for p in gateway.provider_requests if p[1] == "gpt-4o-mini" and not p[2]].__len__() >= 1
    hits = export.series(
        "trajectory.gen_ai.gateway.request.estimated_cost", trajectory__gateway__destination__id=None, **bob
    )
    assert len(hits) == 1
    assert (
        export.total(
            "trajectory.gen_ai.gateway.request.estimated_cost", trajectory__gateway__destination__id=None, **bob
        )
        == 0
    )
    assert export.total("trajectory.gen_ai.gateway.request.estimated_cost", **bob) > 0

    # A model LiteLLM has no price for: usage is recorded, a cost is not.
    unpriced = {"trajectory.gateway.route": "custom-unpriced"}
    assert export.total("gen_ai.client.inference.usage.input_tokens", **unpriced) == 12
    assert export.series("trajectory.gen_ai.client.inference.usage.cost", **unpriced) == []
    assert export.series("trajectory.gen_ai.gateway.request.estimated_cost", **unpriced) == []

    # Each priced client request is charged once, at the gateway's own price.
    assert export.total("trajectory.gen_ai.gateway.request.estimated_cost", **ALICE) == pytest.approx(
        export.total("trajectory.gen_ai.client.inference.usage.cost", **ALICE), rel=1e-9
    )

    # No key, key hash or provider key leaves the process.
    secrets = list(KEYS) + list(PROVIDER_KEYS) + ["sk-master-0000"]
    secrets += [hashlib.sha256(key.encode()).hexdigest() for key in KEYS]
    payload = b"".join(bodies)
    for secret in secrets:
        assert secret.encode() not in payload, secret

    # The spans of the calls say which metrics the tracer recorded, so the backend does not derive them again.
    msgpack = pytest.importorskip("msgpack")
    spans = [span for body in gateway.trace_payloads for trace in msgpack.unpackb(body, raw=False) for span in trace]
    marked = collections.Counter(
        (span["name"], span.get("resource"), (span.get("meta") or {}).get(RECORDED_PROFILES_TAG))
        for span in spans
        if RECORDED_PROFILES_TAG in (span.get("meta") or {})
    )
    assert marked[("litellm.request", "acompletion", ATTEMPT_PROFILES)] >= sent["gpt-4o-mini-flaky"], marked
    assert marked[("litellm.request", "router.acompletion", GATEWAY_PROFILES)] >= 1, marked
    assert {(name, profiles) for name, _, profiles in marked} <= {
        ("litellm.request", ATTEMPT_PROFILES),
        ("litellm.request", GATEWAY_PROFILES),
    }, marked

    # The same scenarios over DogStatsD give the same series.
    gateway.provider.RequestHandlerClass.counters.clear()
    proxy = gateway.proxy(dogstatsd_dir, "dogstatsd")
    try:
        run_scenarios(proxy)
    finally:
        proxy.stop()
    lines = gateway.close()
    assert lines, f"no DogStatsD line received:\n{proxy.output()}"
    assert dogstatsd_series(lines) == export.datadog_series()
    for secret in secrets:
        assert all(secret not in line for line in lines), secret
