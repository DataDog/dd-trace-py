import threading
import types
import typing as t

import pytest

from ddtrace import config
from ddtrace.internal.profiling import lifecycle
from ddtrace.internal.profiling import remoteconfig
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.settings.profiling import config as profiling_config
from ddtrace.internal.settings.profiling import config_str
from ddtrace.profiling import bootstrap
from ddtrace.profiling import profiler as profiler_module


SERVICE: t.Final[str] = "my-service"
ENV: t.Final[str] = "prod"

STACK: t.Final[str] = "_stack_collector_enabled"
MEMORY: t.Final[str] = "_memory_collector_enabled"
LOCK: t.Final[str] = "_lock_collector_enabled"
EXCEPTION: t.Final[str] = "_exception_profiling_enabled"


def _payload(config_id: str, content: t.Optional[dict[str, t.Any]]) -> Payload:
    return Payload(
        metadata=ConfigMetadata(id=config_id, product_name="DEBUG", sha256_hash=None, length=None, tuf_version=1),
        path=f"datadog/2/DEBUG/{config_id}/config",
        content=content,
    )


def _profiling_content(
    target: dict[str, t.Any],
    profilers: t.Optional[dict[str, t.Any]] = None,
    settings: t.Optional[dict[str, t.Any]] = None,
) -> dict[str, t.Any]:
    return {"target": target, "profilers": profilers or {}, "settings": settings or {}}


def _collectors(
    stack: bool = False, memory: bool = False, lock: bool = False, exception: bool = False
) -> dict[str, bool]:
    return {STACK: stack, MEMORY: memory, LOCK: lock, EXCEPTION: exception}


def _env_collectors() -> dict[str, bool]:
    return remoteconfig._wanted_collectors(remoteconfig._get_default_config()["profilers"])


class _Recorder:
    """Stands in for _restart_profiler and for the profiler it starts, which always starts."""

    def __init__(self) -> None:
        self.calls: list[dict[str, bool]] = []
        self.settings: list[dict[str, t.Any]] = []

    def __call__(self, wanted: dict[str, bool], settings: dict[str, t.Any]) -> None:
        self.calls.append(dict(wanted))
        self.settings.append(dict(settings))

    # Before the first restart, the profiler runs with the configuration from the environment.
    def running(self) -> dict[str, bool]:
        return self.calls[-1] if self.calls else _env_collectors()

    def applied_settings(self) -> dict[str, t.Any]:
        return self.settings[-1] if self.settings else remoteconfig._settings_from_env()

    @property
    def last(self) -> dict[str, bool]:
        return self.calls[-1]


def _set_env_profilers(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    stack: bool = True,
    memory: bool = True,
    lock: bool = True,
    exception: bool = True,
) -> None:
    monkeypatch.setattr(profiling_config, "enabled", enabled)
    monkeypatch.setattr(profiling_config.stack, "enabled", stack)
    monkeypatch.setattr(profiling_config.memory, "enabled", memory)
    monkeypatch.setattr(profiling_config.lock, "enabled", lock)
    monkeypatch.setattr(profiling_config.exception, "enabled", exception)
    # The remoteconfig module reads the profilers from the environment when it is imported.
    monkeypatch.setattr(remoteconfig, "_PROFILERS_FROM_ENV", remoteconfig._profilers_from_env())


@pytest.fixture
def restart_profiler(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    monkeypatch.setattr(config, "service", SERVICE)
    monkeypatch.setattr(config, "env", ENV)
    _set_env_profilers(monkeypatch, enabled=False)
    monkeypatch.setattr(remoteconfig, "debug_configs", {})
    recorder = _Recorder()
    monkeypatch.setattr(remoteconfig, "_restart_profiler", recorder)
    monkeypatch.setattr(remoteconfig, "_running_collectors", recorder.running)
    monkeypatch.setattr(remoteconfig, "_applied_settings", recorder.applied_settings)
    return recorder


@pytest.mark.parametrize(
    "less_specific,more_specific",
    [
        # Each scope wins over the less specific scopes.
        ({}, {"env": ENV}),
        ({"env": ENV}, {"tags": ["team:profiling"]}),
        ({"tags": ["team:profiling"]}, {"service": SERVICE}),
        ({"service": SERVICE}, {"tags": ["pod_name:my-pod"]}),
        ({"service": SERVICE}, {"tags": ["container_id:abc"]}),
        # A scope wins over all the less specific scopes together.
        ({"env": ENV, "tags": ["team:profiling"]}, {"service": SERVICE}),
        ({"service": SERVICE, "env": ENV, "tags": ["team:profiling"]}, {"tags": ["pod_name:my-pod"]}),
        # A combination of scopes wins over each of its scopes alone.
        ({"service": SERVICE}, {"service": SERVICE, "env": ENV}),
        ({"service": SERVICE}, {"service": SERVICE, "tags": ["kube_cluster_name:my-cluster"]}),
        # The service and env tags count as the service and env fields.
        ({"tags": ["team:profiling"]}, {"tags": ["service:" + SERVICE]}),
        ({}, {"tags": ["env:" + ENV]}),
        ({"service": SERVICE}, {"tags": ["service:" + SERVICE, "env:" + ENV]}),
    ],
)
def test_specificity_order(less_specific: dict[str, t.Any], more_specific: dict[str, t.Any]) -> None:
    assert remoteconfig._specificity(less_specific) < remoteconfig._specificity(more_specific)


@pytest.mark.parametrize(
    "target,same_as",
    [
        ({"service": "*", "env": "*"}, {}),
        ({"tags": ["service:*", "env:*"]}, {}),
        ({"tags": ["service:" + SERVICE]}, {"service": SERVICE}),
        ({"tags": ["env:" + ENV]}, {"env": ENV}),
        ({"tags": ["pod_name:my-pod", "container_id:abc"]}, {"tags": ["pod_name:my-pod"]}),
        ({"tags": ["team:profiling", "kube_cluster_name:my-cluster"]}, {"tags": ["team:profiling"]}),
    ],
)
def test_specificity_equal(target: dict[str, t.Any], same_as: dict[str, t.Any]) -> None:
    assert remoteconfig._specificity(target) == remoteconfig._specificity(same_as)


@pytest.mark.parametrize(
    "target,local_env,matches",
    [
        ({}, ENV, True),
        ({"service": SERVICE}, ENV, True),
        ({"service": "*"}, ENV, True),
        ({"service": "other-service"}, ENV, False),
        ({"tags": ["service:" + SERVICE]}, ENV, True),
        ({"tags": ["service:other-service"]}, ENV, False),
        ({"service": SERVICE, "tags": ["service:other-service"]}, ENV, False),
        ({"env": ENV}, ENV, True),
        ({"env": "*"}, ENV, True),
        ({"env": "staging"}, ENV, False),
        ({"tags": ["env:staging"]}, ENV, False),
        # The Agent may target configs using an environment configured only at the Agent level.
        ({"env": "staging"}, "", True),
        ({"env": "staging"}, None, True),
        # The other tags are not checked.
        ({"tags": ["pod_name:other-pod", "team:other-team"]}, ENV, True),
    ],
)
def test_target_matches(
    monkeypatch: pytest.MonkeyPatch, target: dict[str, t.Any], local_env: t.Optional[str], matches: bool
) -> None:
    monkeypatch.setattr(config, "service", SERVICE)
    monkeypatch.setattr(config, "env", local_env)
    assert remoteconfig._target_matches(target) is matches


def test_more_specific_config_disables_profiler(restart_profiler: _Recorder) -> None:
    remoteconfig.ProfilingRCCallback()(
        [
            _payload("service", _profiling_content({"service": SERVICE}, {"cpu": False})),
            _payload("env", _profiling_content({"env": ENV}, {"cpu": True, "lock": True})),
        ]
    )

    assert restart_profiler.last[STACK] is False
    assert restart_profiler.last[LOCK] is True


def test_more_specific_config_enables_profiler(restart_profiler: _Recorder) -> None:
    remoteconfig.ProfilingRCCallback()(
        [
            _payload("env", _profiling_content({"env": ENV}, {"cpu": False})),
            _payload("instance", _profiling_content({"tags": ["pod_name:my-pod"]}, {"cpu": True})),
        ]
    )

    assert restart_profiler.last[STACK] is True


def test_unset_profiler_is_a_no_op(restart_profiler: _Recorder) -> None:
    remoteconfig.ProfilingRCCallback()(
        [
            _payload("env", _profiling_content({"env": ENV}, {"cpu": True, "lock": True})),
            _payload("service", _profiling_content({"service": SERVICE}, {"lock": False})),
        ]
    )

    assert restart_profiler.last[STACK] is True
    assert restart_profiler.last[LOCK] is False


def test_non_boolean_profiler_value_is_ignored(restart_profiler: _Recorder) -> None:
    remoteconfig.ProfilingRCCallback()(
        [
            _payload("env", _profiling_content({"env": ENV}, {"cpu": True})),
            _payload("service", _profiling_content({"service": SERVICE}, {"cpu": None})),
        ]
    )

    assert restart_profiler.last[STACK] is True


def test_payload_order_does_not_change_precedence(restart_profiler: _Recorder) -> None:
    more_specific = _payload("service", _profiling_content({"service": SERVICE}, {"cpu": False}))
    less_specific = _payload("env", _profiling_content({"env": ENV}, {"cpu": True, "lock": True}))

    remoteconfig.ProfilingRCCallback()([more_specific, less_specific])

    assert restart_profiler.last[STACK] is False


def test_last_updated_config_wins_on_equal_specificity(restart_profiler: _Recorder) -> None:
    callback = remoteconfig.ProfilingRCCallback()
    callback(
        [
            _payload("a", _profiling_content({"service": SERVICE}, {"cpu": True, "lock": True})),
            _payload("b", _profiling_content({"service": SERVICE}, {"cpu": False})),
        ]
    )
    assert restart_profiler.last[STACK] is False

    callback([_payload("a", _profiling_content({"service": SERVICE}, {"cpu": True}))])
    assert restart_profiler.last[STACK] is True


def test_configs_are_kept_across_callbacks(restart_profiler: _Recorder) -> None:
    callback = remoteconfig.ProfilingRCCallback()
    callback([_payload("env", _profiling_content({"env": ENV}, {"cpu": True}))])
    callback([_payload("service", _profiling_content({"service": SERVICE}, {"lock": True}))])

    assert restart_profiler.last[STACK] is True
    assert restart_profiler.last[LOCK] is True


def test_removed_config_reverts_to_less_specific_config(restart_profiler: _Recorder) -> None:
    callback = remoteconfig.ProfilingRCCallback()
    callback(
        [
            _payload("env", _profiling_content({"env": ENV}, {"cpu": True, "lock": True})),
            _payload("service", _profiling_content({"service": SERVICE}, {"cpu": False})),
        ]
    )
    assert restart_profiler.last[STACK] is False

    callback([_payload("service", None)])

    assert restart_profiler.last[STACK] is True
    assert set(remoteconfig.debug_configs) == {"env"}


def test_removing_all_configs_disables_profilers(restart_profiler: _Recorder) -> None:
    callback = remoteconfig.ProfilingRCCallback()
    callback([_payload("env", _profiling_content({"env": ENV}, {"cpu": True}))])
    callback([_payload("env", None)])

    assert not any(restart_profiler.last.values())
    assert remoteconfig.debug_configs == {}


def test_config_retargeted_away_is_removed(restart_profiler: _Recorder) -> None:
    callback = remoteconfig.ProfilingRCCallback()
    callback([_payload("a", _profiling_content({"service": SERVICE}, {"cpu": True}))])
    assert restart_profiler.last[STACK] is True

    callback([_payload("a", _profiling_content({"service": "other-service"}, {"cpu": True}))])

    assert restart_profiler.last[STACK] is False
    assert remoteconfig.debug_configs == {}


def test_config_for_other_service_is_ignored(restart_profiler: _Recorder) -> None:
    remoteconfig.ProfilingRCCallback()(
        [
            _payload("mine", _profiling_content({"env": ENV}, {"cpu": True})),
            _payload("other", _profiling_content({"tags": ["service:other-service"]}, {"cpu": False})),
        ]
    )

    assert restart_profiler.last[STACK] is True
    assert set(remoteconfig.debug_configs) == {"mine"}


def test_non_profiling_payload_is_ignored(restart_profiler: _Recorder) -> None:
    remoteconfig.ProfilingRCCallback()(
        [
            _payload("probe", {"id": "probe", "type": "LOG_PROBE"}),
            _payload("env", _profiling_content({"env": ENV}, {"cpu": True})),
        ]
    )

    assert restart_profiler.last[STACK] is True
    assert set(remoteconfig.debug_configs) == {"env"}


@pytest.mark.parametrize("tags", [1, {"env": ENV}, "env:prod", [1], ["env:prod", None]])
def test_payload_with_invalid_target_tags_is_ignored(restart_profiler: _Recorder, tags: t.Any) -> None:
    remoteconfig.ProfilingRCCallback()(
        [
            _payload("invalid", _profiling_content({"tags": tags}, {"cpu": False})),
            _payload("env", _profiling_content({"env": ENV}, {"cpu": True})),
        ]
    )

    assert restart_profiler.last[STACK] is True
    assert set(remoteconfig.debug_configs) == {"env"}


def test_settings_are_merged_by_specificity() -> None:
    merged = remoteconfig._merge_configs(
        [
            _profiling_content(
                {"service": SERVICE}, {"cpu": True}, {"maxStackDepth": 32, "uploadIntervalSeconds": None}
            ),
            _profiling_content(
                {"env": ENV}, {}, {"maxStackDepth": 16, "uploadIntervalSeconds": 30, "adaptiveSampling": False}
            ),
        ]
    )

    assert merged["settings"] == {
        **remoteconfig._settings_from_env(),
        "maxStackDepth": 32,
        "uploadIntervalSeconds": 30.0,
        "adaptiveSampling": False,
    }


@pytest.mark.parametrize(
    "key,valid,invalid",
    [
        ("maxStackDepth", 16, True),
        ("maxStackDepth", 16, "32"),
        ("maxStackDepth", 16, -1),
        ("uploadIntervalSeconds", 30, 1.5),
        ("uploadIntervalSeconds", 30, -1),
        ("uploadIntervalSeconds", 30, 10**400),
        ("adaptiveSampling", False, 1),
        ("pythonMemoryMemDomain", False, "false"),
    ],
)
def test_invalid_setting_is_ignored(key: str, valid: t.Any, invalid: t.Any) -> None:
    merged = remoteconfig._merge_configs(
        [
            _profiling_content({"env": ENV}, {}, {key: valid}),
            _profiling_content({"service": SERVICE}, {}, {key: invalid}),
        ]
    )

    assert merged["settings"][key] == valid


def test_zero_upload_interval_uses_the_value_without_remote_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profiling_config.parsed, "upload_interval", 45.0)

    merged = remoteconfig._merge_configs(
        [
            _profiling_content({"env": ENV}, {}, {"uploadIntervalSeconds": 30}),
            _profiling_content({"service": SERVICE}, {}, {"uploadIntervalSeconds": 0}),
        ]
    )

    assert merged["settings"]["uploadIntervalSeconds"] == 45.0


def test_unknown_setting_is_ignored() -> None:
    merged = remoteconfig._merge_configs([_profiling_content({"env": ENV}, {}, {"unknownSetting": 1})])

    assert merged["settings"] == remoteconfig._settings_from_env()


def test_settings_change_restarts_profiler(restart_profiler: _Recorder) -> None:
    callback = remoteconfig.ProfilingRCCallback()
    callback([_payload("env", _profiling_content({"env": ENV}, {"cpu": True}))])

    callback([_payload("env", _profiling_content({"env": ENV}, {"cpu": True}, {"uploadIntervalSeconds": 10}))])

    assert len(restart_profiler.calls) == 2
    assert restart_profiler.calls[0] == restart_profiler.calls[1]
    assert restart_profiler.settings[-1]["uploadIntervalSeconds"] == 10.0


def test_no_restart_when_merged_config_does_not_change(restart_profiler: _Recorder) -> None:
    callback = remoteconfig.ProfilingRCCallback()
    callback([_payload("env", _profiling_content({"env": ENV}, {"cpu": True}))])
    # A less specific config that changes nothing in the merged result.
    callback([_payload("all", _profiling_content({}, {"cpu": True}))])

    assert len(restart_profiler.calls) == 1


@pytest.mark.parametrize(
    "profilers,settings",
    [
        ({"cpu": True}, {"unknownSetting": 20}),
        ({"cpu": True}, {"maxStackDepth": profiling_config.parsed.max_frames}),
        # The stack collector produces both CPU and wall time profiles.
        ({"cpu": False, "wallTime": True}, None),
        ({"cpu": True, "unknown": True}, None),
    ],
)
def test_no_restart_when_running_profiler_has_the_wanted_configuration(
    restart_profiler: _Recorder, profilers: dict[str, t.Any], settings: t.Optional[dict[str, t.Any]]
) -> None:
    callback = remoteconfig.ProfilingRCCallback()
    callback([_payload("env", _profiling_content({"env": ENV}, {"cpu": True}))])

    callback([_payload("env", _profiling_content({"env": ENV}, profilers, settings))])

    assert len(restart_profiler.calls) == 1


def test_default_config_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env_profilers(monkeypatch, enabled=True, stack=True, memory=False, lock=True, exception=False)

    assert remoteconfig._get_default_config()["profilers"] == {
        "cpu": True,
        "wallTime": True,
        "heapSize": False,
        "allocations": False,
        "exception": False,
        "lock": True,
    }


def test_default_settings_are_parsed_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profiling_config.parsed, "max_frames", 32)
    monkeypatch.setattr(profiling_config.stack.parsed, "adaptive_sampling", False)
    # A value remote config applied is not the one from the environment.
    monkeypatch.setattr(profiling_config, "max_frames", 16)

    settings = remoteconfig._get_default_config()["settings"]

    assert settings["maxStackDepth"] == 32
    assert settings["adaptiveSampling"] is False


def test_default_config_is_disabled_when_profiling_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env_profilers(monkeypatch, enabled=False)

    assert not any(remoteconfig._get_default_config()["profilers"].values())


@pytest.mark.parametrize("env_enabled", [True, False])
def test_non_profiling_payload_does_not_restart_profiler(
    monkeypatch: pytest.MonkeyPatch, restart_profiler: _Recorder, env_enabled: bool
) -> None:
    _set_env_profilers(monkeypatch, enabled=env_enabled)

    remoteconfig.ProfilingRCCallback()([_payload("probe", {"id": "probe", "type": "LOG_PROBE"})])

    assert restart_profiler.calls == []


def test_config_matching_env_does_not_restart_profiler(
    monkeypatch: pytest.MonkeyPatch, restart_profiler: _Recorder
) -> None:
    _set_env_profilers(monkeypatch, enabled=True)

    remoteconfig.ProfilingRCCallback()([_payload("env", _profiling_content({"env": ENV}, {"cpu": True, "lock": True}))])

    assert restart_profiler.calls == []


def test_removing_all_configs_reverts_to_env_config(
    monkeypatch: pytest.MonkeyPatch, restart_profiler: _Recorder
) -> None:
    _set_env_profilers(monkeypatch, enabled=True, lock=False)
    callback = remoteconfig.ProfilingRCCallback()

    callback([_payload("env", _profiling_content({"env": ENV}, {"cpu": False, "wallTime": False, "lock": True}))])
    assert restart_profiler.last[STACK] is False
    assert restart_profiler.last[LOCK] is True

    callback([_payload("env", None)])
    assert restart_profiler.last == _env_collectors()


@pytest.fixture
def created_profilers(monkeypatch: pytest.MonkeyPatch) -> t.Iterator[list[dict[str, bool]]]:
    created: list[dict[str, bool]] = []

    class FakeProfiler:
        _active_instance: t.Optional["FakeProfiler"] = None

        def __init__(self, **kwargs: bool) -> None:
            created.append(kwargs)
            self._profiler: types.SimpleNamespace = types.SimpleNamespace(status=ServiceStatus.STOPPED, **kwargs)
            # The real Profiler reads the settings from profiling_config when it is built.
            self.settings: dict[str, t.Any] = remoteconfig._applied_settings()

        def start(self) -> None:
            self._profiler.status = ServiceStatus.RUNNING

        def stop(self, flush: bool = True) -> None:
            self._profiler.status = ServiceStatus.STOPPED

    monkeypatch.setattr(profiler_module, "Profiler", FakeProfiler)
    monkeypatch.delattr(bootstrap, "profiler", raising=False)
    # Restore the settings and collectors _restart_profiler applies to profiling_config.
    for path in [*(path for path, _ in remoteconfig._SETTINGS.values()), *remoteconfig._COLLECTOR_ARGUMENTS.values()]:
        owner, name = remoteconfig._setting_owner(path)
        monkeypatch.setattr(owner, name, getattr(owner, name))
    yield created
    if hasattr(bootstrap, "profiler"):
        del bootstrap.profiler


def test_stop_profiler_stops_and_removes_profiler(created_profilers: list[dict[str, bool]]) -> None:
    started = profiler_module.Profiler()
    started.start()
    lifecycle.set_profiler(started)
    assert lifecycle.current_profiler() is started

    with lifecycle.lock:
        lifecycle.stop_profiler()

    assert started._profiler.status == ServiceStatus.STOPPED
    assert lifecycle.current_profiler() is None
    assert not hasattr(bootstrap, "profiler")


def test_stop_profiler_without_profiler_is_a_no_op(created_profilers: list[dict[str, bool]]) -> None:
    with lifecycle.lock:
        lifecycle.stop_profiler()

    assert lifecycle.current_profiler() is None


def test_start_profiler_replaces_profiler(created_profilers: list[dict[str, bool]]) -> None:
    # Importing sitecustomize starts a profiler, so it is imported once Profiler is replaced.
    from ddtrace.profiling.bootstrap import sitecustomize

    sitecustomize.start_profiler()
    first = bootstrap.profiler
    sitecustomize.start_profiler()

    assert first._profiler.status == ServiceStatus.STOPPED
    assert bootstrap.profiler is not first
    assert bootstrap.profiler._profiler.status == ServiceStatus.RUNNING


@pytest.mark.parametrize(
    "profilers,expected",
    [
        ({"cpu": True}, _collectors(stack=True)),
        ({"wallTime": True}, _collectors(stack=True)),
        ({"heapSize": True}, _collectors(memory=True)),
        ({"allocations": True}, _collectors(memory=True)),
        ({"lock": True}, _collectors(lock=True)),
        ({"exception": True}, _collectors(exception=True)),
        (
            {"cpu": True, "wallTime": False, "heapSize": False, "allocations": True, "lock": True, "exception": True},
            _collectors(stack=True, memory=True, lock=True, exception=True),
        ),
        ({"unknown": True}, _collectors()),
    ],
)
def test_wanted_collectors_maps_profilers_to_collectors(
    monkeypatch: pytest.MonkeyPatch, profilers: dict[str, bool], expected: dict[str, bool]
) -> None:
    monkeypatch.setattr(remoteconfig, "stack_is_available", True)
    monkeypatch.setattr(remoteconfig, "exception_is_available", True)

    assert remoteconfig._wanted_collectors(profilers) == expected


def test_wanted_collectors_keeps_unavailable_collectors_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remoteconfig, "stack_is_available", False)
    monkeypatch.setattr(remoteconfig, "exception_is_available", False)

    profilers = {"cpu": True, "wallTime": True, "heapSize": True, "lock": True, "exception": True}
    assert remoteconfig._wanted_collectors(profilers) == _collectors(memory=True, lock=True)


def test_restart_profiler_starts_wanted_collectors(created_profilers: list[dict[str, bool]]) -> None:
    wanted = _collectors(stack=True, lock=True)

    remoteconfig._restart_profiler(wanted, remoteconfig._settings_from_env())

    assert created_profilers == [wanted]
    assert bootstrap.profiler._profiler.status == ServiceStatus.RUNNING
    assert remoteconfig._running_collectors() == wanted


def test_restart_profiler_without_enabled_collectors_stops_profiler(created_profilers: list[dict[str, bool]]) -> None:
    remoteconfig._restart_profiler(_collectors(stack=True), remoteconfig._settings_from_env())
    started = bootstrap.profiler

    remoteconfig._restart_profiler(_collectors(), remoteconfig._settings_from_env())

    assert len(created_profilers) == 1
    assert started._profiler.status == ServiceStatus.STOPPED
    assert not hasattr(bootstrap, "profiler")
    assert remoteconfig._running_collectors() == _collectors()


def test_restart_profiler_applies_settings_before_building_profiler(created_profilers: list[dict[str, bool]]) -> None:
    settings = {**remoteconfig._settings_from_env(), "maxStackDepth": 32, "pythonMemoryMemDomain": False}

    remoteconfig._restart_profiler(_collectors(stack=True), settings)

    assert profiling_config.max_frames == 32
    assert profiling_config.memory.mem_domain_enabled is False
    assert bootstrap.profiler.settings == settings


def test_restart_profiler_marks_wanted_collectors_enabled(created_profilers: list[dict[str, bool]]) -> None:
    remoteconfig._restart_profiler(_collectors(lock=True), remoteconfig._settings_from_env())

    assert profiling_config.stack.enabled is False
    assert profiling_config.lock.enabled is True
    # The profiler_config tag of the uploaded profiles reads them.
    assert "stack_v2" not in config_str(profiling_config)
    assert "lock" in config_str(profiling_config)


def test_restart_profiler_applies_settings_without_enabled_collectors(created_profilers: list[dict[str, bool]]) -> None:
    remoteconfig._restart_profiler(_collectors(), {**remoteconfig._settings_from_env(), "uploadIntervalSeconds": 10.0})

    assert created_profilers == []
    assert profiling_config.upload_interval == 10.0


def test_start_profiler_waits_for_lifecycle_lock(created_profilers: list[dict[str, bool]]) -> None:
    # Importing sitecustomize starts a profiler, so it is imported once Profiler is replaced.
    from ddtrace.profiling.bootstrap import sitecustomize

    # Stop the profiler before starting the new one.
    with lifecycle.lock:
        lifecycle.stop_profiler()
        created_profilers.clear()

    # The remote config callback holds the lock while it swaps the profiler.
    with lifecycle.lock:
        thread = threading.Thread(target=sitecustomize.start_profiler)
        thread.start()
        thread.join(timeout=0.1)
        assert thread.is_alive()
        assert created_profilers == []

    thread.join()
    assert created_profilers == [{}]
    assert bootstrap.profiler._profiler.status == ServiceStatus.RUNNING


def test_product_activated_when_profiling_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from ddtrace.internal.profiling import product

    monkeypatch.setattr(product.profiling_config, "enabled", False)
    assert not product.activated()

    monkeypatch.setattr(product.profiling_config, "enabled", True)
    assert product.activated()


@pytest.fixture
def callback(
    monkeypatch: pytest.MonkeyPatch, created_profilers: list[dict[str, bool]]
) -> remoteconfig.ProfilingRCCallback:
    monkeypatch.setattr(config, "service", SERVICE)
    monkeypatch.setattr(config, "env", ENV)
    monkeypatch.setattr(remoteconfig, "debug_configs", {})
    return remoteconfig.ProfilingRCCallback()


def _start_bootstrap_profiler(collectors: dict[str, bool]) -> None:
    bootstrap.profiler = profiler_module.Profiler(**collectors)
    bootstrap.profiler.start()


def test_reverting_config_after_failed_restart_restarts_profiler(
    monkeypatch: pytest.MonkeyPatch,
    callback: remoteconfig.ProfilingRCCallback,
    created_profilers: list[dict[str, bool]],
) -> None:
    _set_env_profilers(monkeypatch, enabled=True)
    _start_bootstrap_profiler(_env_collectors())
    env_profiler = bootstrap.profiler

    def fail_start(self: t.Any) -> None:
        raise RuntimeError("cannot start")

    with monkeypatch.context() as m:
        m.setattr(profiler_module.Profiler, "start", fail_start)
        callback([_payload("env", _profiling_content({"env": ENV}, {"lock": False}))])

    assert env_profiler._profiler.status == ServiceStatus.STOPPED
    assert not hasattr(bootstrap, "profiler")

    # The merged configuration is the one from the environment again, but no profiler runs.
    callback([_payload("env", None)])

    assert created_profilers[-1] == _env_collectors()
    assert bootstrap.profiler._profiler.status == ServiceStatus.RUNNING


def test_non_profiling_payload_keeps_auto_started_profiler(
    monkeypatch: pytest.MonkeyPatch, callback: remoteconfig.ProfilingRCCallback
) -> None:
    # ddtrace.profiling.auto starts a profiler even when profiling is disabled in the environment.
    _set_env_profilers(monkeypatch, enabled=False)
    _start_bootstrap_profiler(_collectors(stack=True, memory=True, lock=True, exception=True))
    auto_profiler = bootstrap.profiler

    callback([_payload("probe", {"id": "probe", "type": "LOG_PROBE"})])

    assert bootstrap.profiler is auto_profiler
    assert auto_profiler._profiler.status == ServiceStatus.RUNNING


def test_removing_config_restores_env_collectors(
    monkeypatch: pytest.MonkeyPatch, callback: remoteconfig.ProfilingRCCallback
) -> None:
    _set_env_profilers(monkeypatch, enabled=True, lock=False)
    _start_bootstrap_profiler(_env_collectors())

    callback([_payload("env", _profiling_content({"env": ENV}, {"lock": True}))])
    assert profiling_config.lock.enabled is True

    # The configuration from the environment still has the lock collector disabled.
    callback([_payload("env", None)])

    assert profiling_config.lock.enabled is False
    assert remoteconfig._running_collectors() == _env_collectors()


def test_removing_config_restores_env_settings(
    monkeypatch: pytest.MonkeyPatch, callback: remoteconfig.ProfilingRCCallback
) -> None:
    _set_env_profilers(monkeypatch, enabled=True)
    _start_bootstrap_profiler(_env_collectors())

    callback([_payload("env", _profiling_content({"env": ENV}, {}, {"maxStackDepth": 32}))])

    # Only a setting changed, so the profiler is rebuilt with the same collectors.
    assert remoteconfig._running_collectors() == _env_collectors()
    assert bootstrap.profiler.settings["maxStackDepth"] == 32
    assert profiling_config.max_frames == 32

    callback([_payload("env", None)])

    assert bootstrap.profiler.settings == remoteconfig._settings_from_env()
    assert profiling_config.max_frames == profiling_config.parsed.max_frames


@pytest.mark.subprocess(env={"DD_PROFILING_ENABLED": "false"})
def test_loading_product_does_not_import_profiler() -> None:
    # The product manager loads every product, so this runs in every process, with profiling enabled or not.
    # Importing ddtrace.profiling would load the whole profiler and install its asyncio hooks.
    import sys

    from ddtrace.internal.products import get_product_entry_points

    (entry_point,) = [ep for ep in get_product_entry_points() if ep.name == "profiler"]
    entry_point.load()

    assert "ddtrace.profiling" not in sys.modules
