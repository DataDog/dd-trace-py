from __future__ import annotations

import pytest

from ddtrace.internal.settings.profiling import ProfilingConfig


class TestAdaptiveSamplingConfig:
    def test_defaults(self) -> None:
        config = ProfilingConfig()
        assert config.stack.adaptive_sampling is True
        assert config.stack.adaptive_sampling_target_overhead == 1.0
        assert config.stack.adaptive_sampling_max_interval == 1_000_000

    def test_adaptive_sampling_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED", "0")
        config = ProfilingConfig()
        assert config.stack.adaptive_sampling is False

    def test_adaptive_sampling_target_overhead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_TARGET_OVERHEAD", "5.0")
        config = ProfilingConfig()
        assert config.stack.adaptive_sampling_target_overhead == 5.0

    def test_adaptive_sampling_max_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_MAX_INTERVAL_US", "500000")
        config = ProfilingConfig()
        assert config.stack.adaptive_sampling_max_interval == 500_000

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
            "stack.adaptive_sampling",
            "stack.adaptive_sampling_target_overhead",
            "stack.adaptive_sampling_max_interval",
            "stack.fast_copy",
            "stack.max_threads",
            "exception.enabled",
            "exception.sampling_interval",
        ):
            assert key in settings, f"missing {key!r} in dumped settings"

    def test_keys_are_unprefixed_dotted_paths(self) -> None:
        settings = ProfilingConfig().dump_settings()

        for key in settings:
            assert not key.startswith("DD_"), f"unexpected env-var-style key: {key!r}"
            assert not key.startswith("_DD_"), f"unexpected private env-var-style key: {key!r}"
            assert not key.startswith("dd.profiling."), f"unexpected channel header in key: {key!r}"

    def test_values_are_json_serializable(self) -> None:
        import json

        settings = ProfilingConfig().dump_settings()
        json.dumps(settings)  # must not raise

    def test_reflects_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED", "0")
        monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_TARGET_OVERHEAD", "5.0")
        monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "30.0")

        settings = ProfilingConfig().dump_settings()

        assert settings["stack.adaptive_sampling"] is False
        assert settings["stack.adaptive_sampling_target_overhead"] == 5.0
        assert settings["upload_interval"] == 30.0


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
