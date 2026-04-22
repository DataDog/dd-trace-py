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

    def test_default_is_empty_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """exclude_modules must default to frozenset(), not '' or None."""
        monkeypatch.delenv("DD_PROFILING_LOCK_EXCLUDE_MODULES", raising=False)
        from ddtrace.internal.settings.profiling import ProfilingConfigLock

        cfg = ProfilingConfigLock()
        assert isinstance(cfg.exclude_modules, frozenset)

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
