import types
import typing as t

import pytest

from ddtrace import config
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.settings.profiling import config as profiling_config
from ddtrace.profiling import _product
from ddtrace.profiling import bootstrap
from ddtrace.profiling import profiler as profiler_module


SERVICE = "my-service"
ENV = "prod"


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


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, bool]] = []

    def __call__(self, profilers: dict[str, bool]) -> bool:
        self.calls.append(dict(profilers))
        return True

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


@pytest.fixture
def restart_profiler(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    monkeypatch.setattr(config, "service", SERVICE)
    monkeypatch.setattr(config, "env", ENV)
    _set_env_profilers(monkeypatch, enabled=False)
    monkeypatch.setattr(_product, "debug_configs", {})
    monkeypatch.setattr(_product, "previous_profiling_remote_configuration", None)
    recorder = _Recorder()
    monkeypatch.setattr(_product, "_restart_profiler", recorder)
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
    assert _product._specificity(less_specific) < _product._specificity(more_specific)


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
    assert _product._specificity(target) == _product._specificity(same_as)


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
    assert _product._target_matches(target) is matches


def test_more_specific_config_disables_profiler(restart_profiler: _Recorder) -> None:
    _product.ProfilingRCCallback()(
        [
            _payload("service", _profiling_content({"service": SERVICE}, {"cpu": False})),
            _payload("env", _profiling_content({"env": ENV}, {"cpu": True, "lock": True})),
        ]
    )

    assert restart_profiler.last["cpu"] is False
    assert restart_profiler.last["lock"] is True


def test_more_specific_config_enables_profiler(restart_profiler: _Recorder) -> None:
    _product.ProfilingRCCallback()(
        [
            _payload("env", _profiling_content({"env": ENV}, {"cpu": False})),
            _payload("instance", _profiling_content({"tags": ["pod_name:my-pod"]}, {"cpu": True})),
        ]
    )

    assert restart_profiler.last["cpu"] is True


def test_unset_profiler_is_a_no_op(restart_profiler: _Recorder) -> None:
    _product.ProfilingRCCallback()(
        [
            _payload("env", _profiling_content({"env": ENV}, {"cpu": True, "lock": True})),
            _payload("service", _profiling_content({"service": SERVICE}, {"lock": False})),
        ]
    )

    assert restart_profiler.last["cpu"] is True
    assert restart_profiler.last["lock"] is False


def test_non_boolean_profiler_value_is_ignored(restart_profiler: _Recorder) -> None:
    _product.ProfilingRCCallback()(
        [
            _payload("env", _profiling_content({"env": ENV}, {"cpu": True})),
            _payload("service", _profiling_content({"service": SERVICE}, {"cpu": None})),
        ]
    )

    assert restart_profiler.last["cpu"] is True


def test_payload_order_does_not_change_precedence(restart_profiler: _Recorder) -> None:
    more_specific = _payload("service", _profiling_content({"service": SERVICE}, {"cpu": False}))
    less_specific = _payload("env", _profiling_content({"env": ENV}, {"cpu": True, "lock": True}))

    _product.ProfilingRCCallback()([more_specific, less_specific])

    assert restart_profiler.last["cpu"] is False


def test_last_updated_config_wins_on_equal_specificity(restart_profiler: _Recorder) -> None:
    callback = _product.ProfilingRCCallback()
    callback(
        [
            _payload("a", _profiling_content({"service": SERVICE}, {"cpu": True, "lock": True})),
            _payload("b", _profiling_content({"service": SERVICE}, {"cpu": False})),
        ]
    )
    assert restart_profiler.last["cpu"] is False

    callback([_payload("a", _profiling_content({"service": SERVICE}, {"cpu": True}))])
    assert restart_profiler.last["cpu"] is True


def test_configs_are_kept_across_callbacks(restart_profiler: _Recorder) -> None:
    callback = _product.ProfilingRCCallback()
    callback([_payload("env", _profiling_content({"env": ENV}, {"cpu": True}))])
    callback([_payload("service", _profiling_content({"service": SERVICE}, {"lock": True}))])

    assert restart_profiler.last["cpu"] is True
    assert restart_profiler.last["lock"] is True


def test_removed_config_reverts_to_less_specific_config(restart_profiler: _Recorder) -> None:
    callback = _product.ProfilingRCCallback()
    callback(
        [
            _payload("env", _profiling_content({"env": ENV}, {"cpu": True, "lock": True})),
            _payload("service", _profiling_content({"service": SERVICE}, {"cpu": False})),
        ]
    )
    assert restart_profiler.last["cpu"] is False

    callback([_payload("service", None)])

    assert restart_profiler.last["cpu"] is True
    assert set(_product.debug_configs) == {"env"}


def test_removing_all_configs_disables_profilers(restart_profiler: _Recorder) -> None:
    callback = _product.ProfilingRCCallback()
    callback([_payload("env", _profiling_content({"env": ENV}, {"cpu": True}))])
    callback([_payload("env", None)])

    assert not any(restart_profiler.last.values())
    assert _product.debug_configs == {}


def test_config_retargeted_away_is_removed(restart_profiler: _Recorder) -> None:
    callback = _product.ProfilingRCCallback()
    callback([_payload("a", _profiling_content({"service": SERVICE}, {"cpu": True}))])
    assert restart_profiler.last["cpu"] is True

    callback([_payload("a", _profiling_content({"service": "other-service"}, {"cpu": True}))])

    assert restart_profiler.last["cpu"] is False
    assert _product.debug_configs == {}


def test_config_for_other_service_is_ignored(restart_profiler: _Recorder) -> None:
    _product.ProfilingRCCallback()(
        [
            _payload("mine", _profiling_content({"env": ENV}, {"cpu": True})),
            _payload("other", _profiling_content({"tags": ["service:other-service"]}, {"cpu": False})),
        ]
    )

    assert restart_profiler.last["cpu"] is True
    assert set(_product.debug_configs) == {"mine"}


def test_non_profiling_payload_is_ignored(restart_profiler: _Recorder) -> None:
    _product.ProfilingRCCallback()(
        [
            _payload("probe", {"id": "probe", "type": "LOG_PROBE"}),
            _payload("env", _profiling_content({"env": ENV}, {"cpu": True})),
        ]
    )

    assert restart_profiler.last["cpu"] is True
    assert set(_product.debug_configs) == {"env"}


def test_settings_are_merged_by_specificity(restart_profiler: _Recorder) -> None:
    _product.ProfilingRCCallback()(
        [
            _payload(
                "service",
                _profiling_content({"service": SERVICE}, {"cpu": True}, {"interval": 20, "upload": None}),
            ),
            _payload("env", _profiling_content({"env": ENV}, {}, {"interval": 10, "upload": 60, "max_frames": 64})),
        ]
    )

    merged = _product.previous_profiling_remote_configuration
    assert merged is not None
    assert merged["settings"] == {"interval": 20, "upload": 60, "max_frames": 64}


def test_no_restart_when_merged_config_does_not_change(restart_profiler: _Recorder) -> None:
    callback = _product.ProfilingRCCallback()
    callback([_payload("env", _profiling_content({"env": ENV}, {"cpu": True}))])
    # A less specific config that changes nothing in the merged result.
    callback([_payload("all", _profiling_content({}, {"cpu": True}))])

    assert len(restart_profiler.calls) == 1


def test_default_config_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env_profilers(monkeypatch, enabled=True, stack=True, memory=False, lock=True, exception=False)

    assert _product._get_default_config()["profilers"] == {
        "cpu": True,
        "wallTime": True,
        "heapSize": False,
        "allocations": False,
        "exception": False,
        "lock": True,
    }


def test_default_config_is_disabled_when_profiling_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env_profilers(monkeypatch, enabled=False)

    assert not any(_product._get_default_config()["profilers"].values())


@pytest.mark.parametrize("env_enabled", [True, False])
def test_non_profiling_payload_does_not_restart_profiler(
    monkeypatch: pytest.MonkeyPatch, restart_profiler: _Recorder, env_enabled: bool
) -> None:
    _set_env_profilers(monkeypatch, enabled=env_enabled)

    _product.ProfilingRCCallback()([_payload("probe", {"id": "probe", "type": "LOG_PROBE"})])

    assert restart_profiler.calls == []


def test_config_matching_env_does_not_restart_profiler(
    monkeypatch: pytest.MonkeyPatch, restart_profiler: _Recorder
) -> None:
    _set_env_profilers(monkeypatch, enabled=True)

    _product.ProfilingRCCallback()([_payload("env", _profiling_content({"env": ENV}, {"cpu": True, "lock": True}))])

    assert restart_profiler.calls == []


def test_removing_all_configs_reverts_to_env_config(
    monkeypatch: pytest.MonkeyPatch, restart_profiler: _Recorder
) -> None:
    _set_env_profilers(monkeypatch, enabled=True, lock=False)
    callback = _product.ProfilingRCCallback()

    callback([_payload("env", _profiling_content({"env": ENV}, {"cpu": False, "wallTime": False, "lock": True}))])
    assert restart_profiler.last["cpu"] is False
    assert restart_profiler.last["lock"] is True

    callback([_payload("env", None)])
    assert restart_profiler.last == _product._get_default_config()["profilers"]


@pytest.fixture
def created_profilers(monkeypatch: pytest.MonkeyPatch) -> t.Iterator[list[dict[str, bool]]]:
    created: list[dict[str, bool]] = []

    class FakeProfiler:
        _active_instance: t.Optional["FakeProfiler"] = None

        def __init__(self, **kwargs: bool) -> None:
            created.append(kwargs)
            self._profiler: types.SimpleNamespace = types.SimpleNamespace(status=ServiceStatus.STOPPED)

        def start(self) -> None:
            self._profiler.status = ServiceStatus.RUNNING

        def stop(self, flush: bool = True) -> None:
            self._profiler.status = ServiceStatus.STOPPED

    monkeypatch.setattr(profiler_module, "Profiler", FakeProfiler)
    monkeypatch.delattr(bootstrap, "profiler", raising=False)
    yield created
    if hasattr(bootstrap, "profiler"):
        del bootstrap.profiler


@pytest.mark.parametrize(
    "profilers,expected",
    [
        ({"cpu": True}, {"stack": True, "memory": False, "lock": False, "exception": False}),
        ({"wallTime": True}, {"stack": True, "memory": False, "lock": False, "exception": False}),
        ({"heapSize": True}, {"stack": False, "memory": True, "lock": False, "exception": False}),
        ({"allocations": True}, {"stack": False, "memory": True, "lock": False, "exception": False}),
        ({"lock": True}, {"stack": False, "memory": False, "lock": True, "exception": False}),
        ({"exception": True}, {"stack": False, "memory": False, "lock": False, "exception": True}),
        (
            {"cpu": True, "wallTime": False, "heapSize": False, "allocations": True, "lock": True, "exception": True},
            {"stack": True, "memory": True, "lock": True, "exception": True},
        ),
    ],
)
def test_restart_profiler_maps_profilers_to_collectors(
    created_profilers: list[dict[str, bool]], profilers: dict[str, bool], expected: dict[str, bool]
) -> None:
    assert _product._restart_profiler(profilers) is True

    assert created_profilers == [
        {
            "_stack_collector_enabled": expected["stack"],
            "_memory_collector_enabled": expected["memory"],
            "_lock_collector_enabled": expected["lock"],
            "_exception_profiling_enabled": expected["exception"],
        }
    ]
    assert bootstrap.profiler._profiler.status == ServiceStatus.RUNNING


@pytest.mark.parametrize("profilers", [{}, {"cpu": False, "lock": False}, {"unknown": True}])
def test_restart_profiler_without_enabled_collectors_stops_profiler(
    created_profilers: list[dict[str, bool]], profilers: dict[str, bool]
) -> None:
    assert _product._restart_profiler({"cpu": True}) is True
    started = bootstrap.profiler

    assert _product._restart_profiler(profilers) is True

    assert len(created_profilers) == 1
    assert started._profiler.status == ServiceStatus.STOPPED
    assert not hasattr(bootstrap, "profiler")
