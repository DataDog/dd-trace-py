import os
from unittest.mock import patch

import pytest

from ddtrace.aiguard import AIGuardClient
from ddtrace.aiguard import Message
from ddtrace.aiguard._api_client import _agent_route
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.threads import Thread
from ddtrace.internal.threads import original_Event


@pytest.fixture
def pending_probe():
    entered = original_Event()
    release = original_Event()
    workers = []

    def info():
        entered.set()
        assert release.wait(5), "probe was not released"
        return {"endpoints": [AI_GUARD.AGENT_PROXY_ENDPOINT]}

    def make_thread(*args, **kwargs):
        worker = Thread(*args, **kwargs)
        workers.append(worker)
        return worker

    with (
        patch("ddtrace.aiguard._api_client.Thread", side_effect=make_thread),
        patch("ddtrace.internal.agent.info", side_effect=info) as mock_info,
        patch.object(agent_config, "trace_agent_url", "http://agent:8126"),
        patch.object(agent_config, "trace_agent_timeout_seconds", 5),
    ):
        try:
            _agent_route.start()
            assert entered.wait(5), "background discovery did not start"
            yield release, mock_info, workers
        finally:
            release.set()
            for worker in workers:
                worker.join(5)


def test_evaluation_shares_pending_discovery(pending_probe, tracer):
    release, mock_info, workers = pending_probe
    waiting = original_Event()
    finished = original_Event()
    result = {}
    completed = _agent_route._pending
    original_wait = completed.wait

    def wait(timeout):
        waiting.set()
        return original_wait(timeout)

    def evaluate():
        try:
            client = AIGuardClient("/ai_guard/proxy/api/v2/ai-guard", agent_transport=True)
            result["evaluation"] = client.evaluate([Message(role="user", content="hi")])
        except Exception as error:
            result["error"] = error
        finally:
            finished.set()

    with (
        patch.object(completed, "wait", side_effect=wait),
        patch("ddtrace.aiguard._api_client.HTTPConnection") as connection,
    ):
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"data": {"attributes": {"action": "ALLOW", "tags": []}}}'
        response.getheader.return_value = None
        worker = Thread(target=evaluate, name="test.aiguard.evaluate")
        workers.append(worker)
        worker.start()
        assert waiting.wait(5), "evaluation did not wait for discovery"
        assert not finished.is_set()
        connection.assert_not_called()
        release.set()
        assert finished.wait(5), "evaluation did not finish"

    assert "error" not in result
    assert result["evaluation"]["action"] == "ALLOW"
    connection.assert_called_once()
    mock_info.assert_called_once_with()


def test_pending_discovery_wait_is_bounded(pending_probe):
    _, mock_info, _ = pending_probe
    with patch.object(agent_config, "trace_agent_timeout_seconds", 0):
        base_url, reason = _agent_route.resolve()

    assert base_url is None
    assert "in progress" in reason
    mock_info.assert_called_once_with()


def test_start_is_idempotent_until_reset(pending_probe):
    release, mock_info, workers = pending_probe
    for _ in range(10):
        _agent_route.start()
    assert len(workers) == 1
    release.set()
    assert _agent_route.resolve() == ("http://agent:8126", "")
    for _ in range(10):
        _agent_route.start()
    assert len(workers) == 1
    mock_info.assert_called_once_with()

    _agent_route.reset()
    _agent_route.start()
    assert _agent_route.resolve() == ("http://agent:8126", "")
    assert len(workers) == 2
    assert mock_info.call_count == 2


@pytest.mark.parametrize("change", ["reset", "invalidate"])
def test_state_change_wakes_waiters_and_discards_late_probe(pending_probe, change):
    release, _, workers = pending_probe
    waiting = original_Event()
    finished = original_Event()
    results = []
    completed = _agent_route._pending
    original_wait = completed.wait

    def wait(timeout):
        waiting.set()
        return original_wait(timeout)

    def resolve():
        results.append(_agent_route.resolve())
        finished.set()

    with patch.object(completed, "wait", side_effect=wait):
        worker = Thread(target=resolve, name="test.aiguard.resolve")
        workers.append(worker)
        worker.start()
        assert waiting.wait(5), "caller did not wait for discovery"
        if change == "reset":
            _agent_route.reset()
        else:
            _agent_route.invalidate("route invalidated")
        assert finished.wait(5), "state change did not wake the caller"

    assert results[0][0] is None
    release.set()
    workers[0].join(5)
    assert _agent_route._base_url is None


def test_start_can_retry_after_thread_creation_fails():
    with patch("ddtrace.aiguard._api_client.Thread") as thread:
        thread.return_value.start.side_effect = [RuntimeError("cannot start thread"), None]
        with pytest.raises(RuntimeError, match="cannot start thread"):
            _agent_route.start()
        _agent_route.start()

    assert thread.return_value.start.call_count == 2


@pytest.mark.parametrize("failure", [None, ConnectionFailedError("Agent unavailable")])
def test_transient_discovery_failure_is_retried_by_next_evaluation(failure):
    with (
        patch(
            "ddtrace.internal.agent.info", side_effect=[failure, {"endpoints": [AI_GUARD.AGENT_PROXY_ENDPOINT]}]
        ) as mock_info,
        patch.object(agent_config, "trace_agent_url", "http://agent:8126"),
    ):
        base_url, reason = _agent_route.resolve()
        assert base_url is None
        assert "could not be read" in reason
        assert _agent_route.resolve() == ("http://agent:8126", "")

    assert mock_info.call_count == 2


@pytest.mark.subprocess()
@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_child_rechecks_route_instead_of_inheriting_absence():
    import os
    from unittest.mock import patch
    import warnings

    from ddtrace.aiguard._api_client import _agent_route
    from ddtrace.aiguard._constants import AI_GUARD
    from ddtrace.internal.settings._agent import config as agent_config

    with patch("ddtrace.internal.agent.info", return_value={"endpoints": []}):
        assert _agent_route.resolve()[0] is None

    # Python 3.12+ warns about forking while tracer background threads are running.
    with warnings.catch_warnings(record=True):
        pid = os.fork()
    if pid == 0:
        with (
            patch("ddtrace.internal.agent.info", return_value={"endpoints": [AI_GUARD.AGENT_PROXY_ENDPOINT]}),
            patch.object(agent_config, "trace_agent_url", "http://agent:8126"),
        ):
            base_url, _ = _agent_route.resolve()
        os._exit(0 if base_url == "http://agent:8126" else 1)

    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
