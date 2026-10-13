"""End-to-end tests for LLMObs.routing_context(): multi-tenant routing and dual shipping."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time
from unittest import mock

import pytest

from ddtrace.contrib.internal.futures.patch import patch as patch_futures
from ddtrace.contrib.internal.futures.patch import unpatch as unpatch_futures
from ddtrace.internal.evp_proxy.constants import EVP_PROXY_AGENT_BASE_PATH
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._constants import AGENTLESS_SPAN_BASE_URL
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._constants import SPAN_ENDPOINT
from ddtrace.llmobs._constants import LLMObsExportMode
from ddtrace.llmobs._routing import RoutingTarget
from ddtrace.llmobs._writer import LLMObsSpanWriter


TENANT_A_KEY = "tenant-a-api-key-1234"
TENANT_B_KEY = "tenant-b-api-key-5678"


def _api_key(req):
    # The HTTP client decides the header casing.
    for name, value in req["headers"].items():
        if name.lower() == "dd-api-key":
            return value
    return None


def _sent_events(reqs, initial_count, num_requests, attempts=1000):
    """Wait for num_requests new requests and map each span name to [(api_key, event), ...]."""
    for _ in range(attempts):
        if len(reqs) >= initial_count + num_requests:
            break
        time.sleep(0.001)
    else:
        raise TimeoutError(f"Expected {num_requests} requests, got {len(reqs) - initial_count}")
    sent = {}
    for req in reqs[initial_count:]:
        body = json.loads(req["body"])
        for payload in body if isinstance(body, list) else [body]:
            for event in payload.get("spans", []):
                sent.setdefault(event["name"], []).append((_api_key(req), event))
    return sent


def _keys(sent, name):
    return sorted(key for key, _ in sent.get(name, []))


def test_single_tenant_routing_keeps_llmobs_data_out_of_default_org(llmobs, _llmobs_backend, test_spans):
    _, reqs = _llmobs_backend
    initial_count = len(reqs)
    default_key = llmobs._instance._llmobs_span_writer._api_key

    with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY, dd_site="us5.datadoghq.com"):
        with llmobs.workflow(name="routed-parent"):
            with llmobs.llm(name="routed-child", model_name="gpt-4") as child:
                llmobs.annotate(child, input_data="hello", output_data="world")
    with llmobs.workflow(name="unrouted"):
        pass
    llmobs_service.flush()

    sent = _sent_events(reqs, initial_count, 2)
    assert _keys(sent, "routed-parent") == [TENANT_A_KEY]
    assert _keys(sent, "routed-child") == [TENANT_A_KEY]
    assert _keys(sent, "unrouted") == [default_key]
    for req in reqs[initial_count:]:
        assert TENANT_A_KEY not in req["body"]

    # The APM spans still reach the default org, but must not carry the routed LLMObs payload.
    apm_spans = {s.name: s for s in test_spans.pop()}
    for name in ("routed-parent", "routed-child"):
        assert apm_spans[name]._get_struct_tag(LLMOBS_STRUCT.KEY) is None


def test_dual_shipping_sends_to_every_target_and_not_default_org(llmobs, _llmobs_backend, test_spans):
    _, reqs = _llmobs_backend
    initial_count = len(reqs)
    targets = [
        {"dd_api_key": TENANT_A_KEY, "dd_site": "ddstaging.datadoghq.com"},
        {"dd_api_key": TENANT_B_KEY, "dd_site": "datad0g.com"},
    ]

    with llmobs_service.routing_context(targets=targets):
        with llmobs.workflow(name="dual-shipped"):
            llmobs.annotate(input_data="hello")
    llmobs_service.flush()

    sent = _sent_events(reqs, initial_count, 2)
    assert _keys(sent, "dual-shipped") == sorted([TENANT_A_KEY, TENANT_B_KEY])
    (apm_span,) = test_spans.pop()
    assert apm_span._get_struct_tag(LLMOBS_STRUCT.KEY) is None


def test_concurrent_tenants_are_isolated(llmobs, _llmobs_backend):
    _, reqs = _llmobs_backend
    initial_count = len(reqs)

    async def tenant_work(api_key, name):
        with llmobs_service.routing_context(dd_api_key=api_key):
            with llmobs.workflow(name=name):
                await asyncio.sleep(0.01)
                await asyncio.create_task(asyncio.sleep(0))
                with llmobs.task(name=f"{name}-child"):
                    pass

    async def main():
        await asyncio.gather(tenant_work(TENANT_A_KEY, "tenant-a"), tenant_work(TENANT_B_KEY, "tenant-b"))

    asyncio.run(main())
    llmobs_service.flush()

    sent = _sent_events(reqs, initial_count, 2)
    assert _keys(sent, "tenant-a") == _keys(sent, "tenant-a-child") == [TENANT_A_KEY]
    assert _keys(sent, "tenant-b") == _keys(sent, "tenant-b-child") == [TENANT_B_KEY]


def test_routing_follows_thread_pool_and_does_not_leak_to_reused_worker(llmobs, _llmobs_backend):
    _, reqs = _llmobs_backend
    initial_count = len(reqs)
    default_key = llmobs._instance._llmobs_span_writer._api_key

    def background_work(name):
        with llmobs.workflow(name=name):
            pass

    patch_futures()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            # The worker creates the first LLM span of the trace, as when a web handler offloads
            # the model call, so routing must cross the thread boundary on its own.
            with llmobs._instance.tracer.trace("web.request"):
                with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
                    pool.submit(background_work, "tenant").result()
            # Flush between jobs so a misrouted second job still produces an observable request.
            llmobs_service.flush()
            _sent_events(reqs, initial_count, 1)
            pool.submit(background_work, "default").result()
    finally:
        unpatch_futures()
    llmobs_service.flush()

    sent = _sent_events(reqs, initial_count, 2)
    assert _keys(sent, "tenant") == [TENANT_A_KEY]
    assert _keys(sent, "default") == [default_key]


def test_routed_span_in_apm_agentless_mode_is_built_for_llmobs_intake(llmobs, _llmobs_backend):
    # APM_AGENTLESS events normally ride the APM trace, which carries the error fields and needs
    # dotted tag keys rewritten. A routed event goes to the LLMObs intake instead, so neither applies.
    _, reqs = _llmobs_backend
    initial_count = len(reqs)
    llmobs._instance._export_mode = LLMObsExportMode.APM_AGENTLESS

    with pytest.raises(ValueError):
        with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
            with llmobs.workflow(name="routed-error"):
                llmobs.annotate(tags={"team.name": "ml"})
                raise ValueError("boom")
    llmobs_service.flush()

    ((api_key, event),) = _sent_events(reqs, initial_count, 1)["routed-error"]
    assert api_key == TENANT_A_KEY
    assert event["status"] == "error"
    assert event["meta"]["error"]["message"] == "boom"
    assert "ValueError" in event["meta"]["error"]["type"]
    assert "error:1" in event["tags"]
    assert any(tag.startswith("error_type:") for tag in event["tags"])
    assert "team.name:ml" in event["tags"]


def test_export_span_keeps_llmobs_trace_id_after_routed_span_finishes(llmobs, _llmobs_backend):
    # A routed span drops its meta_struct when it finishes. export_span() must still return the
    # LLMObs trace ID that the tenant received, or evaluations submitted with it cannot join.
    _, reqs = _llmobs_backend
    initial_count = len(reqs)

    with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
        with llmobs.workflow(name="routed-parent"):
            with llmobs.task(name="routed-child") as child:
                pass
            exported = llmobs_service.export_span(child)
    llmobs_service.flush()

    ((_, event),) = _sent_events(reqs, initial_count, 1)["routed-child"]
    assert exported["span_id"] == event["span_id"]
    assert exported["trace_id"] == event["trace_id"]


def test_routed_span_telemetry_keeps_span_attributes(llmobs):
    with mock.patch("ddtrace.llmobs._telemetry.telemetry_writer") as telemetry_writer:
        with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
            with llmobs.workflow(name="routed"):
                pass

    (call,) = [c for c in telemetry_writer.add_count_metric.call_args_list if c.kwargs["name"] == "span.finished"]
    tags = dict(call.kwargs["tags"])
    assert tags["span_kind"] == "workflow"
    assert tags["ml_app"] == "unnamed-ml-app"
    assert tags["is_root_span"] == "1"


@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_sampling_rules='[{"tags": {"tier": "gold"}, "sample_rate": 0}]')]
)
def test_routed_child_and_unrouted_root_share_one_sampling_decision(llmobs, _llmobs_backend):
    _, reqs = _llmobs_backend
    initial_count = len(reqs)

    with llmobs.workflow(name="root") as root:
        llmobs.annotate(root, tags={"tier": "gold"})
        with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
            with llmobs.task(name="routed-child"):
                pass
    llmobs_service.flush()

    sent = _sent_events(reqs, initial_count, 2)
    for name in ("root", "routed-child"):
        ((_, event),) = sent[name]
        assert (event["_dd"]["sample_rate"], event["_dd"]["sampling_decision"]) == ("0", "0")


def test_routed_requests_bypass_agent_and_drop_extra_headers(monkeypatch):
    # The Agent proxy stamps its own API key, and the configured extra headers may hold
    # credentials that a tenant-supplied site must not receive.
    monkeypatch.setenv("_DD_TRACE_WRITER_ADDITIONAL_HEADERS", "Authorization:Bearer-secret-token")
    writer = LLMObsSpanWriter(1.0, 1.0, is_agentless=False, _site="datad0g.com", _api_key="<default-key>")

    intake, endpoint, headers = writer._destination(RoutingTarget(api_key=TENANT_A_KEY, site="us5.datadoghq.com"))

    assert intake == f"{AGENTLESS_SPAN_BASE_URL}.us5.datadoghq.com"
    assert endpoint == SPAN_ENDPOINT
    assert not endpoint.startswith(EVP_PROXY_AGENT_BASE_PATH)
    assert headers == {"Content-Type": "application/json", "DD-API-KEY": TENANT_A_KEY}


@pytest.mark.parametrize(
    "bad_site",
    ["http://evil.example", "evil.example/path", "evil.example:8080", "user@evil.example", "evil example", "EVIL.com"],
)
def test_routing_context_rejects_non_hostname_site(bad_site):
    # dd_site is interpolated into the intake hostname.
    with pytest.raises(ValueError, match="bare Datadog site hostname"):
        with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY, dd_site=bad_site):
            pass
