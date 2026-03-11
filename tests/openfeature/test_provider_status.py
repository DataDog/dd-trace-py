"""
Tests for DataDog Provider status tracking.

Tests that the provider properly implements ProviderStatus:
- NOT_READY by default
- READY when first Remote Config payload is received
- Event emission on status change
- Blocking initialization until config arrives or timeout
"""

import threading
import time

from openfeature import api
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
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def clear_config():
    """Clear FFE configuration before each test."""
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


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
    def test_provider_ready_event_only_once(self):
        """Test that PROVIDER_READY event is only emitted once, not on subsequent configs."""
        ready_events = []

        def on_provider_ready(event_details):
            ready_events.append(event_details)

        api.add_handler(ProviderEvent.PROVIDER_READY, on_provider_ready)

        try:
            with override_global_config({"experimental_flagging_provider_enabled": True}):
                provider = DataDogProvider()
                api.set_provider(provider)

                # Clear events from initialization
                ready_events.clear()

                # First configuration
                config1 = create_config(create_boolean_flag("flag1", enabled=True))
                process_ffe_configuration(config1)

                count_after_first = len(ready_events)
                assert count_after_first >= 1  # Should have emitted

                # Second configuration
                config2 = create_config(create_boolean_flag("flag2", enabled=True))
                process_ffe_configuration(config2)

                count_after_second = len(ready_events)
                # Should not have emitted again
                assert count_after_second == count_after_first
        finally:
            api.remove_handler(ProviderEvent.PROVIDER_READY, on_provider_ready)
            api.clear_providers()

    def test_provider_status_after_shutdown(self):
        """Test that provider returns to NOT_READY after shutdown."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()
            api.set_provider(provider)

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
    def test_config_received_before_initialize(self):
        """Test that provider emits READY if config was received before initialize."""
        ready_events = []

        def on_provider_ready(event_details):
            ready_events.append(event_details)

        with override_global_config({"experimental_flagging_provider_enabled": True}):
            # Create provider and process config before setting it
            provider = DataDogProvider()
            config = create_config(create_boolean_flag("test-flag", enabled=True))
            process_ffe_configuration(config)

            # Now set the provider and add handler
            api.add_handler(ProviderEvent.PROVIDER_READY, on_provider_ready)

            try:
                api.set_provider(provider)

                # Provider should detect existing config and emit READY
                assert provider._status == ProviderStatus.READY
                assert len(ready_events) >= 1
            finally:
                api.remove_handler(ProviderEvent.PROVIDER_READY, on_provider_ready)
                api.clear_providers()


class TestProviderInitializationBlocking:
    """Test that initialize() blocks until config arrives or timeout expires."""

    def test_initialize_blocks_until_config_arrives(self):
        """initialize() should block and return once config is delivered mid-wait."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider(initialization_timeout=5.0)

            # Deliver config from a background thread after 0.5s
            def deliver_config():
                time.sleep(0.5)
                config = create_config(create_boolean_flag("test-flag", enabled=True))
                process_ffe_configuration(config)

            timer = threading.Thread(target=deliver_config, daemon=True)
            timer.start()

            try:
                start = time.monotonic()
                api.set_provider(provider)
                elapsed = time.monotonic() - start

                # Should have blocked for ~0.5s (not instant, not full timeout)
                assert elapsed >= 0.3, f"initialize() returned too fast ({elapsed:.2f}s)"
                assert elapsed < 4.0, f"initialize() took too long ({elapsed:.2f}s), should have unblocked at ~0.5s"
                assert provider._status == ProviderStatus.READY
                assert provider._config_received.is_set()
            finally:
                api.clear_providers()

    def test_initialize_fast_path_when_config_exists(self):
        """initialize() should return immediately if config already exists."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            # Deliver config BEFORE creating provider
            config = create_config(create_boolean_flag("test-flag", enabled=True))
            process_ffe_configuration(config)

            provider = DataDogProvider(initialization_timeout=5.0)

            try:
                start = time.monotonic()
                api.set_provider(provider)
                elapsed = time.monotonic() - start

                # Should be near-instant (config already available)
                assert elapsed < 1.0, f"initialize() took {elapsed:.2f}s, should be instant with pre-loaded config"
                assert provider._status == ProviderStatus.READY
            finally:
                api.clear_providers()

    def test_initialize_timeout_raises(self):
        """initialize() should raise ProviderNotReadyError after timeout expires."""
        from openfeature.exception import ProviderNotReadyError

        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider(initialization_timeout=0.5)

            try:
                start = time.monotonic()
                # set_provider catches the exception and dispatches PROVIDER_ERROR
                api.set_provider(provider)
                elapsed = time.monotonic() - start

                # Should have blocked for ~0.5s (the timeout)
                assert elapsed >= 0.3, f"initialize() returned too fast ({elapsed:.2f}s)"
                assert elapsed < 2.0, f"initialize() took too long ({elapsed:.2f}s)"

                # Provider should be in ERROR state (SDK caught ProviderNotReadyError)
                client = api.get_client()
                assert client.get_provider_status() == ProviderStatus.ERROR
            finally:
                api.clear_providers()

    def test_late_recovery_after_timeout(self):
        """Config arriving after timeout should transition provider to READY."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider(initialization_timeout=0.5)

            try:
                # Let it timeout
                api.set_provider(provider)

                # Provider should be in ERROR state
                client = api.get_client()
                assert client.get_provider_status() == ProviderStatus.ERROR

                # Now deliver config (late recovery)
                config = create_config(create_boolean_flag("test-flag", enabled=True))
                process_ffe_configuration(config)

                # Provider should recover to READY
                assert provider._status == ProviderStatus.READY
                assert provider._config_received.is_set()
            finally:
                api.clear_providers()
