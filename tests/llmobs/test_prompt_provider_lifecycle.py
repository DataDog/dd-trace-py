from concurrent.futures import ThreadPoolExecutor
from threading import Event
from threading import current_thread

from openfeature import api
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._provider import DataDogProvider
from ddtrace.internal.openfeature.writer import get_exposure_writer
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.settings.openfeature import REMOTE_CONFIG
from ddtrace.internal.settings.openfeature import config
from tests.llmobs.test_prompts import _deliver_prompt_flag
from tests.llmobs.test_prompts import _ffe_enabled
from tests.llmobs.test_prompts import _make_manager


@pytest.fixture(autouse=True)
def prompt_provider_environment(monkeypatch):
    api.clear_providers()
    _set_ffe_config(None)
    monkeypatch.setattr(config, "feature_flags_enabled", True)
    monkeypatch.setattr(config, "configuration_source", REMOTE_CONFIG)
    monkeypatch.setattr(config, "exposures_datadog_logging_enabled", True)
    monkeypatch.setattr(config, "ffe_intake_heartbeat_interval", 0.01)
    monkeypatch.setenv("DD_FLAGGING_EVALUATION_COUNTS_ENABLED", "false")
    with _ffe_enabled():
        yield
    api.clear_providers()
    _set_ffe_config(None)


def deliver():
    _deliver_prompt_flag("greeting", {"prompt_id": "greeting", "version": "v1", "template": "managed"})


def test_prompt_provider_recreation_keeps_exposure_worker(monkeypatch):
    import json

    from ddtrace.internal.utils.http import Response

    deliver()
    first = _make_manager(app_key="")
    assert first.get_prompt("greeting", fallback="backup").format() == "managed"
    provider = api.get_client(first._FFE_DOMAIN).provider
    running = get_exposure_writer()
    assert running.status == ServiceStatus.RUNNING
    second = _make_manager(app_key="")
    assert second.get_prompt("greeting", fallback="backup").format() == "managed"
    if hasattr(api, "set_provider_and_wait"):
        assert api.get_client(second._FFE_DOMAIN).provider is provider
        assert get_exposure_writer() is running
    current = get_exposure_writer()
    sent = Event()

    def send(payload, count, route):
        events = json.loads(payload)["exposures"]
        if any(event["subject"]["id"] == "after-recreation" for event in events):
            sent.set()
        return Response(status=200)

    monkeypatch.setattr(current._route_selector, "select", lambda: object())
    monkeypatch.setattr(current, "_send_payload_with_retry", send)
    current.enqueue(
        {
            "timestamp": 1,
            "allocation": {"key": "allocation"},
            "flag": {"key": "greeting"},
            "variant": {"key": "v1"},
            "subject": {"id": "after-recreation"},
        }
    )
    assert sent.wait(3)
    assert current.status == ServiceStatus.RUNNING


def test_prompt_provider_concurrent_manager_creation():
    if not hasattr(api, "set_provider_and_wait"):
        pytest.skip("requires SDK 0.10 provider reuse")
    deliver()

    def lookup(_):
        manager = _make_manager(app_key="")
        assert manager.get_prompt("greeting", fallback="backup").format() == "managed"
        return api.get_client(manager._FFE_DOMAIN).provider

    with ThreadPoolExecutor(max_workers=8) as pool:
        providers = list(pool.map(lookup, range(8)))
    if hasattr(api, "set_provider_and_wait"):
        assert len({id(provider) for provider in providers}) == 1
    assert get_exposure_writer().status == ServiceStatus.RUNNING


def test_prompt_provider_pending_initialization_is_completed(monkeypatch):
    if not hasattr(api, "set_provider_and_wait"):
        pytest.skip("requires asynchronous provider initialization")
    caller = current_thread()
    entered, resume, finished = Event(), Event(), Event()
    initialize = DataDogProvider.initialize

    def delayed_initialize(provider, context):
        if current_thread() is not caller:
            entered.set()
            assert resume.wait(5)
            try:
                return initialize(provider, context)
            finally:
                finished.set()
        return initialize(provider, context)

    monkeypatch.setattr(DataDogProvider, "initialize", delayed_initialize)
    first = _make_manager(app_key="")
    first._ensure_ffe_provider()
    try:
        assert entered.wait(5)
        original = api.get_client(first._FFE_DOMAIN).provider
        deliver()
        second = _make_manager(app_key="")
        assert second.get_prompt("greeting", fallback="backup").format() == "managed"
        assert api.get_client(second._FFE_DOMAIN).provider is original
    finally:
        resume.set()
        assert finished.wait(5)
    assert get_exposure_writer().status == ServiceStatus.RUNNING


def test_prompt_provider_preserves_application_default():
    deliver()
    application_provider = DataDogProvider(initialization_timeout=0)
    register = getattr(api, "set_provider_and_wait", api.set_provider)
    register(application_provider)
    manager = _make_manager(app_key="")
    assert manager.get_prompt("greeting", fallback="backup").format() == "managed"
    assert api.get_client().provider is application_provider
    assert api.get_client(manager._FFE_DOMAIN).provider is not application_provider


def test_prompt_provider_can_register_after_sdk_reset():
    deliver()
    first = _make_manager(app_key="")
    assert first.get_prompt("greeting", fallback="backup").format() == "managed"
    previous = api.get_client(first._FFE_DOMAIN).provider
    api.clear_providers()
    second = _make_manager(app_key="")
    assert second.get_prompt("greeting", fallback="backup").format() == "managed"
    assert api.get_client(second._FFE_DOMAIN).provider is not previous
    assert get_exposure_writer().status == ServiceStatus.RUNNING


@pytest.mark.subprocess(timeout=20)
def test_prompt_provider_registration_lock_resets_after_fork():
    import os
    import select
    import signal
    from threading import Event
    from threading import Thread

    from ddtrace.internal.openfeature._provider import _DOMAIN_PROVIDER_LOCK

    held, release = Event(), Event()

    def hold():
        with _DOMAIN_PROVIDER_LOCK:
            held.set()
            assert release.wait(10)

    holder = Thread(target=hold)
    holder.start()
    assert held.wait(3)
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        with _DOMAIN_PROVIDER_LOCK:
            os.write(write_fd, b"1")
        os._exit(0)
    os.close(write_fd)
    completed = False
    try:
        assert select.select([read_fd], [], [], 5)[0]
        assert os.read(read_fd, 1) == b"1"
        completed = True
    finally:
        release.set()
        holder.join(timeout=3)
        os.close(read_fd)
        if not completed:
            os.kill(child, signal.SIGKILL)
        os.waitpid(child, 0)


def test_prompt_provider_overlapping_agentless_initialization(monkeypatch):
    from unittest.mock import Mock

    from ddtrace.internal.settings.openfeature import AGENTLESS

    if not hasattr(api, "set_provider_and_wait"):
        pytest.skip("requires asynchronous provider initialization")
    monkeypatch.setattr(config, "configuration_source", AGENTLESS)
    entered, resume, second_init, second_factory, finished = (Event() for _ in range(5))
    sources = []
    initialize = DataDogProvider.initialize

    def factory(*args):
        source = Mock()
        sources.append(source)
        if len(sources) == 1:
            entered.set()
            assert resume.wait(5)
        else:
            second_factory.set()
        return source

    def observe_initialize(provider, context):
        if entered.is_set():
            second_init.set()
            return initialize(provider, context)
        try:
            return initialize(provider, context)
        finally:
            finished.set()

    monkeypatch.setattr("ddtrace.internal.openfeature._source_selection.create_agentless_source", factory)
    monkeypatch.setattr(DataDogProvider, "initialize", observe_initialize)
    first = _make_manager(app_key="")
    first._ensure_ffe_provider()
    try:
        assert entered.wait(5)
        deliver()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_make_manager(app_key="").get_prompt, "greeting", fallback="backup")
            try:
                assert second_init.wait(5)
                assert not second_factory.wait(0.1)
            finally:
                resume.set()
            assert future.result(timeout=5).format() == "managed"
        assert finished.wait(5)
        assert len(sources) == 1
    finally:
        resume.set()
        finished.wait(5)
        api.clear_providers()
    sources[0].start.assert_called_once()
    sources[0].stop.assert_called_once()
