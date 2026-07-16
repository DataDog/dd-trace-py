"""End-to-end tests for the native (libdatadog) Remote Config client.

These exercise the real flow — the native fetcher talking HTTP to a mock agent,
parsing the agent's ``/v0.7/config`` response, and dispatching the diffed changes
to Python product callbacks — plus the cross-process broadcast path (origin
publishes a snapshot to shared memory; a forked child consumes it via the native
reader). The TUF parsing/reconciliation itself is unit-tested in libdatadog's
Rust crate; here we validate the Python integration and the SHM/fork wiring.
"""

import base64
import copy
import hashlib
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import json
import os
import threading

import pytest

from ddtrace.internal.native import RemoteConfigCapabilities
from ddtrace.internal.native import RemoteConfigProduct
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.remoteconfig.client import RemoteConfigClient


HERE = os.path.dirname(__file__)
RESPONSES = json.load(open(os.path.join(HERE, "rc_mocked_responses_asm_features.json")))

BASE_PATH = "datadog/2/ASM_FEATURES/ASM_FEATURES-base/config"
SECOND_PATH = "datadog/2/ASM_FEATURES/ASM_FEATURES-second/config"


class _Sink(RCCallback):
    def __init__(self):
        self.batches = []

    def __call__(self, payloads):
        self.batches.append(list(payloads))

    @property
    def last(self):
        return self.batches[-1] if self.batches else []


class _MockAgent:
    """Serves a queue of ``/v0.7/config`` responses (one per POST)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0
        self.requests = []

        agent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _send(self, payload: bytes):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                self._send(json.dumps({"endpoints": ["/v0.7/config"]}).encode())

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                agent.requests.append(json.loads(self.rfile.read(length) or b"{}"))
                # Hold at the last response once exhausted.
                idx = min(agent._idx, len(agent._responses) - 1)
                agent._idx += 1
                self._send(json.dumps(agent._responses[idx]).encode())

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_a):
        self._server.shutdown()
        self._server.server_close()


def _client(agent_url, product=RemoteConfigProduct.AsmFeatures):
    client = RemoteConfigClient()
    client.agent_url = agent_url
    sink = _Sink()
    client.register_callback(product, sink)
    client.enable_product(product)
    return client, sink


def test_apply_configuration():
    # responses[1] assigns the ASM_FEATURES base config.
    with _MockAgent([RESPONSES[1]]) as agent:
        client, sink = _client(agent.url)
        assert client.request() is True

    payloads = sink.last
    assert len(payloads) == 1
    assert payloads[0].metadata.product_name == "ASM_FEATURES"
    assert payloads[0].path == BASE_PATH
    assert payloads[0].content == {"asm": {"enabled": True}}


def test_remove_configuration():
    # responses[1] adds the base config; responses[3] (client_configs=None,
    # higher targets version) removes it -> the callback observes content=None.
    with _MockAgent([RESPONSES[1], RESPONSES[3]]) as agent:
        client, sink = _client(agent.url)
        assert client.request() is True  # apply
        assert client.request() is True  # remove

    assert [p.path for p in sink.batches[0]] == [BASE_PATH]
    removals = [p for p in sink.batches[-1] if p.content is None]
    assert any(p.path == BASE_PATH for p in removals), "base config should be removed (content None)"


def test_multiple_configs():
    # responses[4] assigns base + second.
    with _MockAgent([RESPONSES[4]]) as agent:
        client, sink = _client(agent.url)
        assert client.request() is True

    paths = sorted(p.path for p in sink.last)
    assert paths == sorted([BASE_PATH, SECOND_PATH])


def test_no_delta_on_unchanged_repoll():
    with _MockAgent([RESPONSES[1]]) as agent:
        client, sink = _client(agent.url)
        assert client.request() is True  # apply -> 1 payload
        assert client.request() is True  # same version cached -> no new payloads

    # Only the applying poll invokes the product callback; the unchanged re-poll
    # yields no delta, so __call__ is not invoked again (with an empty batch).
    assert len(sink.batches) == 1
    assert len(sink.batches[0]) == 1


def test_enabled_products_reported_to_agent():
    with _MockAgent([RESPONSES[1]]) as agent:
        client, _ = _client(agent.url)
        client.request()

    assert agent.requests, "agent received no request"
    products = agent.requests[0]["client"]["products"]
    assert "ASM_FEATURES" in products


def test_capabilities_reported_to_agent():
    # Capabilities are passed as native RemoteConfigCapabilities enum values (no
    # Python-side bit mask) and encoded natively into the request.
    with _MockAgent([RESPONSES[1]]) as agent:
        client, _ = _client(agent.url)
        client.add_capabilities([RemoteConfigCapabilities.AsmActivation, RemoteConfigCapabilities.LlmObsActivation])
        client.request()

    caps = bytes(agent.requests[0]["client"]["capabilities"])
    expected = (
        (1 << int(RemoteConfigCapabilities.AsmActivation)) | (1 << int(RemoteConfigCapabilities.LlmObsActivation))
    ).to_bytes(7, "big")
    assert caps == expected


def test_update_capabilities_replaces_within_mask():
    # update_capabilities replaces the bits within `mask` with the given subset,
    # so it can *clear* previously-advertised capabilities (unlike add_capabilities)
    # while leaving capabilities outside the mask untouched.
    with _MockAgent([RESPONSES[1], RESPONSES[1]]) as agent:
        client, _ = _client(agent.url)
        client.add_capabilities([RemoteConfigCapabilities.LlmObsActivation])
        mask = [RemoteConfigCapabilities.AsmActivation, RemoteConfigCapabilities.AsmIpBlocking]
        client.update_capabilities(mask, [RemoteConfigCapabilities.AsmActivation])
        client.request()

        # Now deactivate: clear the ASM bits within the same mask.
        client.update_capabilities(mask, [])
        client.request()

    first = int.from_bytes(bytes(agent.requests[0]["client"]["capabilities"]), "big")
    assert first & (1 << int(RemoteConfigCapabilities.AsmActivation))  # set within mask
    assert first & (1 << int(RemoteConfigCapabilities.LlmObsActivation))  # untouched, outside mask
    assert not first & (1 << int(RemoteConfigCapabilities.AsmIpBlocking))  # in mask but not active

    second = int.from_bytes(bytes(agent.requests[1]["client"]["capabilities"]), "big")
    assert not second & (1 << int(RemoteConfigCapabilities.AsmActivation))  # cleared
    assert second & (1 << int(RemoteConfigCapabilities.LlmObsActivation))  # still untouched


def _config_state(agent, request_index, config_id="ASM_FEATURES-base"):
    """Extract a config's reported apply state from a captured outbound request.

    ``config_states`` are built from the previous poll's applied set, so they
    first appear on the poll *after* the config was received.
    """
    state = agent.requests[request_index]["client"].get("state") or {}
    for cs in state.get("config_states") or []:
        if cs["id"] == config_id:
            return cs
    return None


def _malformed_response():
    """A response whose target file is hash-valid but not valid JSON.

    Content integrity (base64 + sha256) is verified in libdatadog, so the hash
    in the signed targets must match; TUF signatures are not verified against a
    trusted agent, so patching the targets blob in place is sufficient.
    """
    resp = copy.deepcopy(RESPONSES[1])
    content = b"this-is-not-json{{{"
    resp["target_files"][0]["raw"] = base64.b64encode(content).decode()
    targets = json.loads(base64.b64decode(resp["targets"]))
    entry = targets["signed"]["targets"][BASE_PATH]
    entry["hashes"]["sha256"] = hashlib.sha256(content).hexdigest()
    entry["length"] = len(content)
    resp["targets"] = base64.b64encode(json.dumps(targets).encode()).decode()
    return resp


def test_apply_state_acknowledged_reported_to_agent():
    # A config is reported to the agent as ACKNOWLEDGED (apply_state=2) only
    # after it has been applied. Apply is synchronous in the polling process, so
    # by the poll that reports the state the product callback has already run.
    # See PR #18750 (acknowledge only after the product applies the config).
    with _MockAgent([RESPONSES[1]]) as agent:  # holds at the last response when exhausted
        client, sink = _client(agent.url)
        assert client.request() is True  # poll 1: receive + apply base config
        assert client.request() is True  # poll 2: report applied state to the agent

    # Poll 1 carried no config_states yet (nothing applied); poll 2 reports it.
    assert _config_state(agent, 0) is None
    applied = _config_state(agent, 1)
    assert applied is not None and applied["apply_state"] == 2, agent.requests[1]
    assert not applied.get("apply_error")


def test_apply_state_acknowledged_even_when_callback_raises():
    # A delivered config is acknowledged (apply_state=2) regardless of whether
    # the product callback raised: the failure is surfaced via the product's own
    # telemetry, not by leaving the config un-acknowledged. Malformed content is
    # the only case reported as errored (see next test). Per PR #18750.
    class _RaisingSink(RCCallback):
        def __call__(self, payloads):
            raise RuntimeError("callback boom")

    with _MockAgent([RESPONSES[1]]) as agent:
        client = RemoteConfigClient()
        client.agent_url = agent.url
        client.register_callback(RemoteConfigProduct.AsmFeatures, _RaisingSink())
        client.enable_product(RemoteConfigProduct.AsmFeatures)
        assert client.request() is True  # callback raises but is contained
        assert client.request() is True

    applied = _config_state(agent, 1)
    assert applied is not None and applied["apply_state"] == 2, agent.requests[1]


def test_apply_state_error_reported_for_malformed_content():
    # A hash-valid config whose content is not valid JSON cannot be deserialized;
    # it is the one case reported to the agent as ERROR (apply_state=3) with an
    # apply_error, and it is never dispatched to the product callback. Per PR #18750.
    with _MockAgent([_malformed_response()]) as agent:
        client, sink = _client(agent.url)
        assert client.request() is True
        assert client.request() is True

    errored = _config_state(agent, 1)
    assert errored is not None and errored["apply_state"] == 3, agent.requests[1]
    assert errored.get("apply_error")
    # The malformed config is never handed to the product callback.
    assert sink.batches == [] or all(p.path != BASE_PATH for b in sink.batches for p in b)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_fork_broadcast():
    # Origin applies the config and publishes the snapshot to shared memory; a
    # forked child consumes it via the native reader without contacting the agent.
    with _MockAgent([RESPONSES[1]]) as agent:
        client, _ = _client(agent.url)
        client.enable_shared_memory()
        assert client.request() is True

        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:  # child
            os.close(read_fd)
            try:
                sink = _Sink()
                client._product_callbacks[RemoteConfigProduct.AsmFeatures] = sink
                reader = client.make_reader()
                client.dispatch_native_changes(reader.read(client.enabled_product_names()))
                got = sink.last
                msg = "%d|%s|%s" % (
                    len(got),
                    got[0].path if got else "",
                    json.dumps(got[0].content) if got else "",
                )
                os.write(write_fd, msg.encode())
            finally:
                os.close(write_fd)
                os._exit(0)

        os.close(write_fd)
        out = os.read(read_fd, 4096).decode()
        os.close(read_fd)
        os.waitpid(pid, 0)

    count, path, content = out.split("|", 2)
    assert count == "1"
    assert path == BASE_PATH
    assert json.loads(content) == {"asm": {"enabled": True}}


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_fork_of_fork_broadcast():
    # A grandchild (a fork of an already-forked consumer) must still receive the
    # full active snapshot. The intermediate child consumed the snapshot into its
    # reader's diff memo and took the SHM handles from the native client, so the
    # grandchild can neither re-take the handles nor rely on a fresh diff:
    # make_reader() must reuse the inherited reader and reset its memo.
    with _MockAgent([RESPONSES[1]]) as agent:
        client, _ = _client(agent.url)
        client.enable_shared_memory()
        assert client.request() is True

        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:  # intermediate child
            os.close(read_fd)
            try:
                # Consume the snapshot here first: this populates the reader's
                # diff memo and takes the SHM handles, so without a reset the
                # grandchild would observe no delta.
                reader = client.make_reader()
                reader.read(client.enabled_product_names())

                cpid = os.fork()
                if cpid == 0:  # grandchild
                    sink = _Sink()
                    client._product_callbacks[RemoteConfigProduct.AsmFeatures] = sink
                    # Reuses the inherited reader (handles already gone) and resets it.
                    greader = client.make_reader()
                    client.dispatch_native_changes(greader.read(client.enabled_product_names()))
                    got = sink.last
                    msg = "%d|%s|%s" % (
                        len(got),
                        got[0].path if got else "",
                        json.dumps(got[0].content) if got else "",
                    )
                    os.write(write_fd, msg.encode())
                    os._exit(0)
                os.waitpid(cpid, 0)
            finally:
                os.close(write_fd)
                os._exit(0)

        os.close(write_fd)
        out = os.read(read_fd, 4096).decode()
        os.close(read_fd)
        os.waitpid(pid, 0)

    count, path, content = out.split("|", 2)
    assert count == "1", "grandchild must receive the full snapshot, not an empty delta"
    assert path == BASE_PATH
    assert json.loads(content) == {"asm": {"enabled": True}}


def test_poller_runs_periodic_every_cycle_even_when_fetch_fails():
    # Product periodic() housekeeping must run every poller cycle regardless of
    # fetch outcome, so stale-state timeouts (e.g. tracer-flare expiry, DI
    # status) keep firing while the agent is down. Regression guard: periodic()
    # used to run only after a successful fetch, inside _dispatch_to_products.
    from ddtrace.internal.remoteconfig.worker import RemoteConfigPoller

    ticks = []

    class _PeriodicSink(_Sink):
        def periodic(self):
            ticks.append(1)

    poller = RemoteConfigPoller()
    poller._client._product_callbacks[RemoteConfigProduct.AsmFeatures] = _PeriodicSink()
    # Simulate agent-down / failed fetch: the state machine dispatches nothing.
    poller._state = lambda: None

    poller.periodic()
    poller.periodic()

    assert ticks == [1, 1]


def test_enable_builds_native_runtime_before_registering_fork_hook(monkeypatch):
    # The native client + shared Tokio runtime must be constructed at enable(),
    # BEFORE the _before_fork hook is registered. Otherwise the runtime registers
    # its own before_fork/after_fork hooks lazily inside _before_fork — after
    # forksafe has snapshotted the before-fork registry — so a child forked
    # before the first poll runs after_fork_child with no matching before_fork
    # and inherits an unusable runtime.
    from ddtrace.internal.remoteconfig import worker as worker_mod
    from tests.utils import override_global_config

    poller = worker_mod.RemoteConfigPoller()
    order = []
    monkeypatch.setattr(poller._client, "ensure_native", lambda: order.append("native"))
    monkeypatch.setattr(poller, "start", lambda: order.append("start"))
    monkeypatch.setattr(worker_mod.forksafe, "register_before_fork", lambda hook: order.append("before_fork"))

    with override_global_config(dict(_remote_config_enabled=True)):
        assert poller.enable() is True

    assert order == ["native", "before_fork", "start"], order
