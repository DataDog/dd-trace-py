from __future__ import annotations

import json
import typing as t

import pytest

from ddtrace.internal.settings.profiling import ProfilingConfig
from ddtrace.internal.settings.profiling import ProfilingConfigLock
from ddtrace.internal.settings.profiling import ProfilingConfigMemory
from ddtrace.internal.settings.profiling import ProfilingConfigStack
from ddtrace.internal.settings.profiling import config_str
from ddtrace.internal.settings.profiling import lock_max_frames
from ddtrace.internal.settings.profiling import memory_max_frames
from ddtrace.internal.settings.profiling import stack_max_frames


class TestMaxFramesConfig:
    def test_default(self) -> None:
        assert ProfilingConfig().max_frames == 64

    @pytest.mark.parametrize("value", (600, 601, 10_000))
    def test_clamps_to_backend_limit(self, monkeypatch: pytest.MonkeyPatch, value: int) -> None:
        monkeypatch.setenv("DD_PROFILING_MAX_FRAMES", str(value))

        # One of the backend's 600 locations is reserved for the omitted-frame indicator.
        assert ProfilingConfig().max_frames == 599

    @pytest.mark.parametrize("value", (512, 599))
    def test_preserves_value_within_backend_limit(self, monkeypatch: pytest.MonkeyPatch, value: int) -> None:
        monkeypatch.setenv("DD_PROFILING_MAX_FRAMES", str(value))

        assert ProfilingConfig().max_frames == value


class TestMemoryMaxFramesConfig:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DD_PROFILING_MAX_FRAMES", raising=False)
        monkeypatch.delenv("DD_PROFILING_MEMORY_MAX_FRAMES", raising=False)

    @pytest.fixture
    def memory_default_256(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ProfilingConfigMemory.max_frames, "default", 256)

    def test_default_is_unset(self) -> None:
        config: ProfilingConfig = ProfilingConfig()

        assert config.memory.max_frames is None
        assert memory_max_frames(config) == 64

    def test_explicit_memory_wins_over_explicit_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_MAX_FRAMES", "128")
        monkeypatch.setenv("DD_PROFILING_MEMORY_MAX_FRAMES", "32")

        assert memory_max_frames(ProfilingConfig()) == 32

    @pytest.mark.usefixtures("memory_default_256")
    def test_explicit_global_wins_over_memory_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_MAX_FRAMES", "128")

        assert memory_max_frames(ProfilingConfig()) == 128

    @pytest.mark.usefixtures("memory_default_256")
    def test_memory_default_wins_over_global_default(self) -> None:
        assert memory_max_frames(ProfilingConfig()) == 256

    def test_global_default_when_nothing_set(self) -> None:
        config: ProfilingConfig = ProfilingConfig()

        assert memory_max_frames(config) == config.max_frames == 64

    @pytest.mark.parametrize("value", (600, 10_000))
    def test_clamps_to_backend_limit(self, monkeypatch: pytest.MonkeyPatch, value: int) -> None:
        monkeypatch.setenv("DD_PROFILING_MEMORY_MAX_FRAMES", str(value))
        config: ProfilingConfig = ProfilingConfig()

        assert config.memory.max_frames == 599
        assert memory_max_frames(config) == 599

    def test_config_str_omits_tag_when_values_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_MEMORY_MAX_FRAMES", "64")

        assert "MEMMAXF" not in config_str(ProfilingConfig())

    def test_config_str_omits_tag_by_default(self) -> None:
        assert "MEMMAXF" not in config_str(ProfilingConfig())

    def test_config_str_includes_tag_when_values_differ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_MEMORY_MAX_FRAMES", "256")
        features: list[str] = config_str(ProfilingConfig()).split("_")

        assert "MAXF64" in features
        assert "MEMMAXF256" in features

    @pytest.mark.parametrize(
        "global_env,memory_env,expected",
        (
            (None, None, 64),
            ("128", None, 128),
            (None, "256", 256),
            ("128", "32", 32),
        ),
    )
    def test_memory_collector_uses_resolved_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
        global_env: t.Optional[str],
        memory_env: t.Optional[str],
        expected: int,
    ) -> None:
        from ddtrace.profiling.collector import memalloc

        if global_env is not None:
            monkeypatch.setenv("DD_PROFILING_MAX_FRAMES", global_env)
        if memory_env is not None:
            monkeypatch.setenv("DD_PROFILING_MEMORY_MAX_FRAMES", memory_env)
        monkeypatch.setattr(memalloc, "config", ProfilingConfig())

        assert memalloc.MemoryCollector().max_nframe == expected


class TestStackMaxFramesConfig:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DD_PROFILING_MAX_FRAMES", raising=False)
        monkeypatch.delenv("DD_PROFILING_STACK_MAX_FRAMES", raising=False)

    @pytest.fixture
    def stack_default_256(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ProfilingConfigStack.max_frames, "default", 256)

    def test_default_is_unset(self) -> None:
        config: ProfilingConfig = ProfilingConfig()

        assert config.stack.max_frames is None
        assert stack_max_frames(config) == 64

    def test_explicit_stack_wins_over_explicit_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_MAX_FRAMES", "128")
        monkeypatch.setenv("DD_PROFILING_STACK_MAX_FRAMES", "32")

        assert stack_max_frames(ProfilingConfig()) == 32

    @pytest.mark.usefixtures("stack_default_256")
    def test_explicit_global_wins_over_stack_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_MAX_FRAMES", "128")

        assert stack_max_frames(ProfilingConfig()) == 128

    @pytest.mark.usefixtures("stack_default_256")
    def test_stack_default_wins_over_global_default(self) -> None:
        assert stack_max_frames(ProfilingConfig()) == 256

    def test_global_default_when_nothing_set(self) -> None:
        config: ProfilingConfig = ProfilingConfig()

        assert stack_max_frames(config) == config.max_frames == 64

    @pytest.mark.parametrize("value", (600, 10_000))
    def test_clamps_to_backend_limit(self, monkeypatch: pytest.MonkeyPatch, value: int) -> None:
        monkeypatch.setenv("DD_PROFILING_STACK_MAX_FRAMES", str(value))
        config: ProfilingConfig = ProfilingConfig()

        assert config.stack.max_frames == 599
        assert stack_max_frames(config) == 599

    def test_config_str_omits_tag_when_values_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_STACK_MAX_FRAMES", "64")

        assert "STACKMAXF" not in config_str(ProfilingConfig())

    def test_config_str_omits_tag_by_default(self) -> None:
        assert "STACKMAXF" not in config_str(ProfilingConfig())

    def test_config_str_includes_tag_when_values_differ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_STACK_MAX_FRAMES", "256")
        features: list[str] = config_str(ProfilingConfig()).split("_")

        assert "MAXF64" in features
        assert "STACKMAXF256" in features

    @pytest.mark.parametrize(
        "global_env,stack_env,expected",
        (
            (None, None, 64),
            ("128", None, 128),
            (None, "256", 256),
            ("128", "32", 32),
        ),
    )
    def test_stack_collector_uses_resolved_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
        global_env: t.Optional[str],
        stack_env: t.Optional[str],
        expected: int,
    ) -> None:
        from ddtrace.profiling.collector import stack as stack_collector

        if global_env is not None:
            monkeypatch.setenv("DD_PROFILING_MAX_FRAMES", global_env)
        if stack_env is not None:
            monkeypatch.setenv("DD_PROFILING_STACK_MAX_FRAMES", stack_env)
        monkeypatch.setattr(stack_collector, "config", ProfilingConfig())

        assert stack_collector.StackCollector().nframes == expected


class TestLockMaxFramesConfig:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DD_PROFILING_MAX_FRAMES", raising=False)
        monkeypatch.delenv("DD_PROFILING_LOCK_MAX_FRAMES", raising=False)

    @pytest.fixture
    def lock_default_256(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ProfilingConfigLock.max_frames, "default", 256)

    def test_default_is_unset(self) -> None:
        config: ProfilingConfig = ProfilingConfig()

        assert config.lock.max_frames is None
        assert lock_max_frames(config) == 64

    def test_explicit_lock_wins_over_explicit_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_MAX_FRAMES", "128")
        monkeypatch.setenv("DD_PROFILING_LOCK_MAX_FRAMES", "32")

        assert lock_max_frames(ProfilingConfig()) == 32

    @pytest.mark.usefixtures("lock_default_256")
    def test_explicit_global_wins_over_lock_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_MAX_FRAMES", "128")

        assert lock_max_frames(ProfilingConfig()) == 128

    @pytest.mark.usefixtures("lock_default_256")
    def test_lock_default_wins_over_global_default(self) -> None:
        assert lock_max_frames(ProfilingConfig()) == 256

    def test_global_default_when_nothing_set(self) -> None:
        config: ProfilingConfig = ProfilingConfig()

        assert lock_max_frames(config) == config.max_frames == 64

    @pytest.mark.parametrize("value", (600, 10_000))
    def test_clamps_to_backend_limit(self, monkeypatch: pytest.MonkeyPatch, value: int) -> None:
        monkeypatch.setenv("DD_PROFILING_LOCK_MAX_FRAMES", str(value))
        config: ProfilingConfig = ProfilingConfig()

        assert config.lock.max_frames == 599
        assert lock_max_frames(config) == 599

    def test_config_str_omits_tag_when_values_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_LOCK_MAX_FRAMES", "64")

        assert "LOCKMAXF" not in config_str(ProfilingConfig())

    def test_config_str_omits_tag_by_default(self) -> None:
        assert "LOCKMAXF" not in config_str(ProfilingConfig())

    def test_config_str_includes_tag_when_values_differ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_LOCK_MAX_FRAMES", "256")
        features: list[str] = config_str(ProfilingConfig()).split("_")

        assert "MAXF64" in features
        assert "LOCKMAXF256" in features


class TestAdaptiveSamplingConfig:
    def test_defaults(self) -> None:
        config = ProfilingConfig()
        assert config.stack.adaptive_sampling is True
        assert config.stack.adaptive_sampling_target_overhead == 1.0
        assert config.stack.adaptive_sampling_max_interval == 100_000

    def test_adaptive_sampling_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED", "0")
        config = ProfilingConfig()
        assert config.stack.adaptive_sampling is False

    def test_adaptive_sampling_target_overhead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_TARGET_OVERHEAD", "5.0")
        config = ProfilingConfig()
        assert config.stack.adaptive_sampling_target_overhead == 5.0

    def test_adaptive_sampling_max_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_MAX_INTERVAL_US", "50000")
        config = ProfilingConfig()
        assert config.stack.adaptive_sampling_max_interval == 50_000

    def test_adaptive_sampling_target_overhead_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Target overhead must be at least 1%
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_TARGET_OVERHEAD", "0.5")
        with pytest.raises(ValueError):
            ProfilingConfig()

        # Target overhead must be less than 100%
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_TARGET_OVERHEAD", "101")
        with pytest.raises(ValueError):
            ProfilingConfig()

    def test_adaptive_sampling_max_interval_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Max interval must be at least 100us
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_MAX_INTERVAL_US", "99")
        with pytest.raises(ValueError):
            ProfilingConfig()

        # Max interval must be less than 1s
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_MAX_INTERVAL_US", "2000000")
        with pytest.raises(ValueError):
            ProfilingConfig()


class TestGCFramesConfig:
    def test_default(self) -> None:
        assert ProfilingConfig().stack.gc_enabled is False

    def test_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_STACK_GC_ENABLED", "true")

        assert ProfilingConfig().stack.gc_enabled is True


class TestExcludeModulesConfig:
    """Unit tests for the exclude_modules config field type guarantees."""

    def test_default_is_populated_frozenset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """exclude_modules must default to a known frozenset of modules, not '' or None."""
        monkeypatch.delenv("DD_PROFILING_LOCK_EXCLUDE_MODULES", raising=False)
        from ddtrace.internal.settings.profiling import ProfilingConfigLock

        expected = frozenset(
            {
                "anyio",
                "asyncio",
                "bytecode",
                "concurrent",
                "datadog",
                "ddsketch",
                "ddtrace",
                "envier",
                "gunicorn",
                "h11",
                "http",
                "logging",
                "threading",
                "uvicorn",
                "werkzeug",
                "wrapt",
            }
        )
        cfg = ProfilingConfigLock()
        assert isinstance(cfg.exclude_modules, frozenset)
        assert cfg.exclude_modules == expected, (
            f"Default exclude_modules changed. Update this test if intentional.\n"
            f"  Missing: {expected - cfg.exclude_modules}\n"
            f"  Extra:   {cfg.exclude_modules - expected}"
        )

    def test_parsed_value_is_frozenset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-empty env var must produce a frozenset[str], not a raw string."""
        monkeypatch.setenv("DD_PROFILING_LOCK_EXCLUDE_MODULES", "uvicorn,asyncio,sqlalchemy.pool")
        from ddtrace.internal.settings.profiling import ProfilingConfigLock

        cfg = ProfilingConfigLock()
        assert isinstance(cfg.exclude_modules, frozenset)
        assert cfg.exclude_modules == frozenset({"uvicorn", "asyncio", "sqlalchemy.pool"})

    def test_whitespace_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Leading/trailing whitespace around module names must be stripped."""
        monkeypatch.setenv("DD_PROFILING_LOCK_EXCLUDE_MODULES", " uvicorn , asyncio ")
        from ddtrace.internal.settings.profiling import ProfilingConfigLock

        cfg = ProfilingConfigLock()
        assert cfg.exclude_modules == frozenset({"uvicorn", "asyncio"})


def test_always_excluded_modules_contains_required_entries() -> None:
    """_ALWAYS_EXCLUDED_MODULES must always contain the core stdlib concurrency modules.

    These modules are excluded unconditionally to prevent double-counting and
    to ensure stdlib-internal locks (e.g. inside Condition/Semaphore) stay native.
    """
    from ddtrace.profiling.collector._lock import _ALWAYS_EXCLUDED_MODULES

    assert "threading" in _ALWAYS_EXCLUDED_MODULES
    assert "asyncio" in _ALWAYS_EXCLUDED_MODULES
    assert "concurrent" in _ALWAYS_EXCLUDED_MODULES


class TestDumpSettings:
    """Tests for the profiler-settings serializer used in per-profile metadata."""

    def test_includes_private_keys(self) -> None:
        settings = ProfilingConfig().dump_settings()

        for key in (
            "enabled",
            "upload_interval",
            "stack.enabled",
            "stack.gc_enabled",
            "stack.adaptive_sampling",
            "stack.adaptive_sampling_target_overhead",
            "stack.adaptive_sampling_max_interval",
            "stack.fast_copy",
            "stack.max_threads",
            "stack.max_frames",
            "lock.max_frames",
            "memory.max_frames",
            "exception.enabled",
            "exception.sampling_interval",
        ):
            assert key in settings, f"missing {key!r} in dumped settings"

        assert settings["stack.max_frames"] is None
        assert settings["lock.max_frames"] is None
        assert settings["memory.max_frames"] is None

    def test_keys_are_unprefixed_dotted_paths(self) -> None:
        settings = ProfilingConfig().dump_settings()

        for key in settings:
            assert not key.startswith("DD_"), f"unexpected env-var-style key: {key!r}"
            assert not key.startswith("_DD_"), f"unexpected private env-var-style key: {key!r}"
            assert not key.startswith("dd.profiling."), f"unexpected channel header in key: {key!r}"

    def test_values_are_json_serializable(self) -> None:
        settings = ProfilingConfig().dump_settings()
        json.dumps(settings)  # must not raise

    def test_reflects_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED", "0")
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_TARGET_OVERHEAD", "5.0")
        monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "30.0")
        monkeypatch.setenv("DD_PROFILING_STACK_MAX_FRAMES", "32")
        monkeypatch.setenv("DD_PROFILING_LOCK_MAX_FRAMES", "48")
        monkeypatch.setenv("DD_PROFILING_MEMORY_MAX_FRAMES", "16")

        settings = ProfilingConfig().dump_settings()

        assert settings["stack.adaptive_sampling"] is False
        assert settings["stack.adaptive_sampling_target_overhead"] == 5.0
        assert settings["upload_interval"] == 30.0
        assert settings["stack.max_frames"] == 32
        assert settings["lock.max_frames"] == 48
        assert settings["memory.max_frames"] == 16


@pytest.mark.subprocess(env=dict(DD_PROFILING_LOCK_EXCLUDE_MODULES=""))
def test_always_excluded_modules_cannot_be_overridden() -> None:
    """Even with an empty user exclude list, stdlib modules (threading, asyncio, concurrent)
    remain excluded via _ALWAYS_EXCLUDED_MODULES — the internal lock inside Condition
    must be a native lock.
    """
    import threading

    from ddtrace.profiling.collector._lock import _ProfiledLock
    from ddtrace.profiling.collector.threading import ThreadingLockCollector
    from ddtrace.profiling.collector.threading import ThreadingSemaphoreCollector
    from tests.profiling.collector.test_utils import init_ddup

    init_ddup("test_always_excluded")

    with ThreadingLockCollector(capture_pct=100), ThreadingSemaphoreCollector(capture_pct=100):
        sem = threading.Semaphore(1)
        assert isinstance(sem, _ProfiledLock), "User semaphore should be profiled"

        internal_lock = sem._cond._lock
        assert not isinstance(internal_lock, _ProfiledLock), (
            "Stdlib-internal lock must remain native even when DD_PROFILING_LOCK_EXCLUDE_MODULES is empty"
        )


class TestEmbeddedInterpreterFastCopy:
    def test_is_python_embedded_returns_false_for_python(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.internal.settings.profiling import _is_python_embedded

        monkeypatch.setattr("os.readlink", lambda _: "/usr/bin/python3.12")
        monkeypatch.setattr("sys.platform", "linux")
        assert _is_python_embedded() is False

    def test_is_python_embedded_returns_true_for_non_python(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.internal.settings.profiling import _is_python_embedded

        monkeypatch.setattr("os.readlink", lambda _: "/ucsr/bin/nginx")
        monkeypatch.setattr("sys.platform", "linux")
        assert _is_python_embedded() is True

    def test_is_python_embedded_fallback_to_sys_executable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.internal.settings.profiling import _is_python_embedded

        def _raise_oserror(_: str) -> str:
            raise OSError("no procfs")

        monkeypatch.setattr("os.readlink", _raise_oserror)
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("sys.executable", "/opt/myapp/bin/myapp")
        assert _is_python_embedded() is True

    def test_is_python_embedded_fallback_empty_executable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.internal.settings.profiling import _is_python_embedded

        def _raise_oserror(_: str) -> str:
            raise OSError("no procfs")

        monkeypatch.setattr("os.readlink", _raise_oserror)
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("sys.executable", "")
        assert _is_python_embedded() is True

    def test_non_linux_falls_back_to_sys_executable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.internal.settings.profiling import _is_python_embedded

        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("sys.executable", "/usr/local/bin/python3")
        assert _is_python_embedded() is False

    def test_non_linux_embedded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.internal.settings.profiling import _is_python_embedded

        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("sys.executable", "/Applications/MyGame.app/Contents/MacOS/mygame")
        assert _is_python_embedded() is True


class TestMemDomainConfig:
    """Config plumbing for PYMEM_DOMAIN_MEM heap-profiler hooks."""

    def test_default_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED", raising=False)
        config: ProfilingConfig = ProfilingConfig()
        assert config.memory.mem_domain_enabled is True

    def test_disabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED", "false")
        config: ProfilingConfig = ProfilingConfig()
        assert config.memory.mem_domain_enabled is False


class TestNativeHeapConfig:
    """Config plumbing for experimental native (C/C++) heap profiling.

    These assert the runtime gate only; they are platform-independent and do not
    require the (opt-in, Linux-only) gotter cdylib to be present.
    """

    def test_default_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DD_PROFILING_NATIVE_HEAP_ENABLED", raising=False)
        config: ProfilingConfig = ProfilingConfig()
        assert config.native_heap.enabled is False

    def test_enabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_PROFILING_NATIVE_HEAP_ENABLED", "true")
        config: ProfilingConfig = ProfilingConfig()
        assert config.native_heap.enabled is True

    def test_config_str_includes_tag_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.internal.settings.profiling import config_str

        monkeypatch.setenv("DD_PROFILING_NATIVE_HEAP_ENABLED", "true")
        assert "nativeheap" in config_str(ProfilingConfig())

    def test_config_str_omits_tag_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from ddtrace.internal.settings.profiling import config_str

        monkeypatch.delenv("DD_PROFILING_NATIVE_HEAP_ENABLED", raising=False)
        assert "nativeheap" not in config_str(ProfilingConfig())


class TestNativeHeapActivator:
    """The ctypes activator must not raise on import."""

    def test_import_does_not_raise(self) -> None:
        from ddtrace.internal.datadog.profiling import heap_gotter

        assert isinstance(heap_gotter.is_available, bool)
        assert isinstance(heap_gotter.failure_msg, str)
        # Do not call install() here: GOT patching is permanent and would poison
        # the shared pytest worker. That path is covered by the subprocess
        # smoke/fork tests in test_native_heap_gotter.py.
