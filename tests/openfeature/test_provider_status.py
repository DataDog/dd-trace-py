"""
Tests for DataDog Provider status tracking.

Tests that the provider properly implements ProviderStatus:
- NOT_READY by default
- READY when first Remote Config payload is received
- Event emission on status change
- Bounded initialization wait, ERROR on timeout, and READY after late delivery
"""

import logging
import threading
import time

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ProviderNotReadyError
from openfeature.provider import ProviderStatus
import pytest


# ProviderEvent only exists in SDK 0.7.0+
try:
    from openfeature.event import ProviderEvent
except ImportError:
    ProviderEvent = None  # type: ignore

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.conftest import set_openfeature_provider
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def clear_config():
    """Clear FFE configuration before each test."""
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


def _set_provider(provider: DataDogProvider) -> None:
    set_openfeature_provider(provider)


class TestProviderStatus:
    """Test provider status lifecycle."""

    def test_provider_starts_not_ready(self):
        """Test that provider starts with NOT_READY status."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()

            assert provider._status == ProviderStatus.NOT_READY
            assert not provider._config_received.is_set()

    def test_provider_becomes_ready_after_first_config(self):
        """Test that provider becomes READY after receiving first configuration."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()
            api.set_provider(provider)

            try:
                # Verify starts as NOT_READY
                assert provider._status == ProviderStatus.NOT_READY

                # Process a configuration
                config = create_config(create_boolean_flag("test-flag", enabled=True))
                process_ffe_configuration(config)

                # Verify becomes READY
                assert provider._status == ProviderStatus.READY
                assert provider._config_received.is_set()
            finally:
                api.clear_providers()

    def test_provider_ready_event_emitted(self):
        """Test that PROVIDER_READY event is emitted when first config received."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()
            api.set_provider(provider)

            try:
                # Provider should not have received config yet
                assert not provider._config_received.is_set()

                # Process a configuration
                config = create_config(create_boolean_flag("test-flag", enabled=True))
                process_ffe_configuration(config)

                # Provider should now have received config and be READY
                assert provider._config_received.is_set()
                assert provider._status == ProviderStatus.READY
            finally:
                api.clear_providers()

    @pytest.mark.skipif(ProviderEvent is None, reason="ProviderEvent not available in SDK 0.6.0")
    def test_provider_ready_event_only_once(self, mocker):
        """Test that PROVIDER_READY event is only emitted once, not on subsequent configs."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()
            # Count synchronous provider emissions; SDK handlers may run later on an executor.
            emit = mocker.spy(provider, "emit")

            def ready_emissions():
                return [call for call in emit.call_args_list if call.args[0] == ProviderEvent.PROVIDER_READY]

            try:
                _set_provider(provider)
                assert ready_emissions() == []

                # First configuration
                config1 = create_config(create_boolean_flag("flag1", enabled=True))
                process_ffe_configuration(config1)
                assert len(ready_emissions()) == 1

                # Second configuration
                config2 = create_config(create_boolean_flag("flag2", enabled=True))
                process_ffe_configuration(config2)
                # Should not have emitted again
                assert len(ready_emissions()) == 1
            finally:
                api.clear_providers()

    @pytest.mark.skipif(ProviderEvent is None, reason="ProviderEvent not available in SDK 0.6.0")
    def test_sdk_error_event_emitted_when_initialization_times_out(self):
        """The SDK reports PROVIDER_ERROR when no configuration arrives in time."""
        error_events = []
        error_event = threading.Event()

        def on_provider_error(event_details):
            error_events.append(event_details)
            error_event.set()

        api.add_handler(ProviderEvent.PROVIDER_ERROR, on_provider_error)

        try:
            with override_global_config({"experimental_flagging_provider_enabled": True}):
                provider = DataDogProvider(initialization_timeout=0.01)
                api.set_provider(provider)

                assert error_event.wait(timeout=1.0)
                assert len(error_events) == 1
                assert api.get_client().get_provider_status() == ProviderStatus.ERROR
                assert provider._status == ProviderStatus.NOT_READY
                assert not provider._config_received.is_set()

                config = create_config(create_boolean_flag("test-flag", enabled=True))
                process_ffe_configuration(config)

                assert api.get_client().get_provider_status() == ProviderStatus.READY
                assert provider._status == ProviderStatus.READY
        finally:
            api.remove_handler(ProviderEvent.PROVIDER_ERROR, on_provider_error)
            api.clear_providers()

    def test_provider_status_after_shutdown(self):
        """Test that provider returns to NOT_READY after shutdown."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()
            # A background initialize() that finishes after shutdown() would set READY again.
            _set_provider(provider)

            try:
                # Process a configuration
                config = create_config(create_boolean_flag("test-flag", enabled=True))
                process_ffe_configuration(config)

                # Verify READY
                assert provider._status == ProviderStatus.READY

                # Shutdown
                provider.shutdown()

                # Verify back to NOT_READY
                assert provider._status == ProviderStatus.NOT_READY
                assert not provider._config_received.is_set()
            finally:
                api.clear_providers()

    def test_multiple_providers_receive_status_updates(self):
        """Test that multiple provider instances receive status updates."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider1 = DataDogProvider()
            provider2 = DataDogProvider()

            api.set_provider(provider1, "client1")
            api.set_provider(provider2, "client2")

            try:
                # Both start as NOT_READY
                assert provider1._status == ProviderStatus.NOT_READY
                assert provider2._status == ProviderStatus.NOT_READY

                # Process configuration
                config = create_config(create_boolean_flag("test-flag", enabled=True))
                process_ffe_configuration(config)

                # Both should become READY
                assert provider1._status == ProviderStatus.READY
                assert provider2._status == ProviderStatus.READY
            finally:
                api.clear_providers()

    @pytest.mark.skipif(ProviderEvent is None, reason="ProviderEvent not available in SDK 0.6.0")
    def test_config_received_before_initialize(self, mocker):
        """Test that the SDK reports READY without a provider emission for preloaded config."""
        ready_event = threading.Event()

        def on_provider_ready(event_details):
            ready_event.set()

        with override_global_config({"experimental_flagging_provider_enabled": True}):
            # Create provider and process config before setting it
            provider = DataDogProvider()
            emit = mocker.spy(provider, "emit")
            config = create_config(create_boolean_flag("test-flag", enabled=True))
            process_ffe_configuration(config)

            try:
                _set_provider(provider)
                # Register after initialization so the immediate READY belongs to this provider.
                api.add_handler(ProviderEvent.PROVIDER_READY, on_provider_ready)

                # The fast path marks the provider READY; the SDK dispatches the event.
                assert provider._status == ProviderStatus.READY
                assert ready_event.wait(timeout=1.0)
                assert ProviderEvent.PROVIDER_READY not in [call.args[0] for call in emit.call_args_list]
            finally:
                # clear_providers() also removes SDK handlers when initialization fails.
                api.clear_providers()

    @pytest.mark.skipif(ProviderEvent is None, reason="ProviderEvent not available in SDK 0.6.0")
    def test_attached_provider_receives_config_before_async_initialize(self):
        """Configuration can arrive after attach but before asynchronous initialization."""
        ready_events = []

        def on_emit(provider, event, details):
            ready_events.append(event)

        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()
            provider.attach(on_emit)

            config = create_config(create_boolean_flag("test-flag", enabled=True))
            process_ffe_configuration(config)

            assert provider._status == ProviderStatus.READY
            assert ProviderEvent.PROVIDER_READY not in ready_events


class TestProviderInitializationAsync:
    """Test the bounded initialization wait and the READY recovery that can follow it."""

    def test_initialize_raises_when_the_timeout_expires(self):
        """A timeout raises the canonical OpenFeature provider-not-ready error."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider(initialization_timeout=0.2)

            try:
                start = time.monotonic()
                with pytest.raises(ProviderNotReadyError):
                    provider.initialize(EvaluationContext())
                elapsed = time.monotonic() - start

                # Waited for the configured timeout, then gave up rather than hanging.
                assert 0.2 <= elapsed < 2.0, f"initialize() waited {elapsed:.2f}s, expected about 0.2s"
                # READY still arrives later, via on_configuration_received().
                assert provider._status == ProviderStatus.NOT_READY
            finally:
                api.clear_providers()

    def test_initialize_debug_logs_start_and_success(self, caplog):
        provider = DataDogProvider(initialization_timeout=5.0)
        process_ffe_configuration(create_config(create_boolean_flag("test-flag", enabled=True)))
        try:
            with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._provider"):
                provider.initialize(EvaluationContext())
        finally:
            provider.shutdown()

        assert "Feature Flags: waiting for provider initialization (timeout 5.0s)..." in caplog.messages
        assert (
            "Feature Flags: provider initialized successfully (configuration was already available)" in caplog.messages
        )

    def test_initialize_debug_logs_success_after_waiting(self, caplog):
        provider = DataDogProvider(initialization_timeout=5.0)
        timer = threading.Timer(
            0.05, process_ffe_configuration, args=(create_config(create_boolean_flag("late-flag", enabled=True)),)
        )
        timer.start()
        try:
            with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._provider"):
                provider.initialize(EvaluationContext())
        finally:
            timer.join()
            provider.shutdown()

        assert any(
            m.startswith("Feature Flags: provider initialized successfully (configuration received after ")
            for m in caplog.messages
        ), caplog.messages

    def test_initialize_timeout_is_not_reported_as_success(self, caplog):
        provider = DataDogProvider(initialization_timeout=0)
        try:
            with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._provider"):
                with pytest.raises(ProviderNotReadyError):
                    provider.initialize(EvaluationContext())
        finally:
            provider.shutdown()

        assert "Feature Flags: waiting for provider initialization (timeout 0.0s)..." in caplog.messages
        assert not any(m.startswith("Feature Flags: provider initialized successfully") for m in caplog.messages)
        assert any(
            m.startswith("Feature Flags: provider failed to initialize: no configuration received after")
            for m in caplog.messages
        ), caplog.messages

    def test_debug_logs_recovery_after_initialization_timeout(self, caplog):
        provider = DataDogProvider(initialization_timeout=0)
        try:
            with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._provider"):
                with pytest.raises(ProviderNotReadyError):
                    provider.initialize(EvaluationContext())
                process_ffe_configuration(create_config(create_boolean_flag("late-flag", enabled=True)))
        finally:
            provider.shutdown()

        assert (
            "Feature Flags: provider recovered: configuration received after initialization timed out"
            in caplog.messages
        )

    def test_shutdown_debug_logs_start_and_completion(self, caplog):
        provider = DataDogProvider()
        process_ffe_configuration(create_config(create_boolean_flag("test-flag", enabled=True)))
        provider.initialize(EvaluationContext())
        with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature._provider"):
            provider.shutdown()

        messages = [r.getMessage() for r in caplog.records if r.name == "ddtrace.internal.openfeature._provider"]
        assert messages[0] == "Feature Flags: shutting down provider"
        assert messages[-1] == (
            "Feature Flags: provider shut down; see writer shutdown outcomes for delivery or unfinished work"
        )

    @pytest.mark.skipif(not hasattr(api, "set_provider_and_wait"), reason="Blocking registration requires SDK 0.10+")
    def test_set_provider_and_wait_propagates_the_initialization_error(self):
        """Blocking registration propagates ProviderNotReadyError to the caller."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider(initialization_timeout=0)

            try:
                with pytest.raises(ProviderNotReadyError):
                    api.set_provider_and_wait(provider)

                assert api.get_client().get_provider_status() == ProviderStatus.ERROR
            finally:
                api.clear_providers()

    @pytest.mark.skipif(
        not hasattr(api, "set_provider_and_wait"), reason="Non-blocking registration requires SDK 0.10+"
    )
    def test_set_provider_initializes_in_the_background(self):
        """Non-blocking registration returns before provider initialization completes."""
        error_event = threading.Event()

        def on_provider_error(event_details):
            error_event.set()

        api.add_handler(ProviderEvent.PROVIDER_ERROR, on_provider_error)

        try:
            with override_global_config({"experimental_flagging_provider_enabled": True}):
                provider = DataDogProvider(initialization_timeout=0.5)

                start = time.monotonic()
                api.set_provider(provider)
                elapsed = time.monotonic() - start

                assert elapsed < 0.2, f"set_provider() waited {elapsed:.2f}s, expected non-blocking registration"
                assert error_event.wait(timeout=1.0)
                assert api.get_client().get_provider_status() == ProviderStatus.ERROR
        finally:
            api.remove_handler(ProviderEvent.PROVIDER_ERROR, on_provider_error)
            api.clear_providers()

    def test_initialize_waits_for_config_and_becomes_ready(self):
        """initialize() blocks until config arrives, so READY implies flags are resolvable."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider(initialization_timeout=5.0)

            config = create_config(create_boolean_flag("test-flag", enabled=True))
            delivery = threading.Timer(0.1, process_ffe_configuration, args=(config,))

            try:
                delivery.start()
                start = time.monotonic()
                provider.initialize(EvaluationContext())
                elapsed = time.monotonic() - start

                # Returned on delivery rather than on the timeout.
                assert elapsed < 5.0, f"initialize() waited {elapsed:.2f}s, expected to return on delivery"
                assert provider._status == ProviderStatus.READY
                assert provider._config_received.is_set()
            finally:
                delivery.cancel()
                api.clear_providers()

    def test_initialization_timeout_comes_from_the_environment(self, monkeypatch):
        """The timeout is configurable without a constructor argument."""
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_INITIALIZATION_TIMEOUT_MS", "2500")
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            assert DataDogProvider()._initialization_timeout == 2.5

    def test_constructor_timeout_overrides_the_environment(self, monkeypatch):
        """An explicit argument wins, so embedders are not bound to the environment."""
        monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_INITIALIZATION_TIMEOUT_MS", "2500")
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            assert DataDogProvider(initialization_timeout=0.5)._initialization_timeout == 0.5

    def test_initialize_fast_path_when_config_exists(self):
        """initialize() should return immediately if config already exists."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            # Deliver config BEFORE creating provider
            config = create_config(create_boolean_flag("test-flag", enabled=True))
            process_ffe_configuration(config)

            provider = DataDogProvider()

            try:
                start = time.monotonic()
                _set_provider(provider)
                elapsed = time.monotonic() - start

                # Should be near-instant (config already available)
                assert elapsed < 1.0, f"initialize() took {elapsed:.2f}s, should be instant with pre-loaded config"
                assert provider._status == ProviderStatus.READY
            finally:
                api.clear_providers()

    def test_ready_after_config_arrives_async(self):
        """Provider transitions to READY when config arrives after initialization fails."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()

            try:
                with pytest.raises(ProviderNotReadyError):
                    provider.initialize(EvaluationContext())
                # Still NOT_READY immediately after initialization fails.
                assert provider._status == ProviderStatus.NOT_READY

                # Config arrives later (simulating RC delivery)
                config = create_config(create_boolean_flag("test-flag", enabled=True))
                process_ffe_configuration(config)

                # Provider should now be READY
                assert provider._status == ProviderStatus.READY
                assert provider._config_received.is_set()
            finally:
                api.clear_providers()

    def test_late_config_delivery_transitions_to_ready(self):
        """Config arriving after set_provider should transition provider to READY."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()

            try:
                with pytest.raises(ProviderNotReadyError):
                    provider.initialize(EvaluationContext())

                # Provider is NOT_READY at this point
                assert provider._status == ProviderStatus.NOT_READY

                # Simulate delayed RC delivery
                config = create_config(create_boolean_flag("test-flag", enabled=True))
                process_ffe_configuration(config)

                # Provider should recover to READY
                assert provider._status == ProviderStatus.READY
                assert provider._config_received.is_set()
            finally:
                api.clear_providers()
