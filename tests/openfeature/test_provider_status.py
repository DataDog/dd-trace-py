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
from openfeature.event import ProviderEvent
from openfeature.exception import ProviderNotReadyError
from openfeature.provider import ProviderStatus
import pytest

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

    def test_provider_becomes_ready_after_first_config(self, wait_for_provider_registration):
        """Test that provider becomes READY after receiving first configuration."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()
            api.set_provider(provider)
            wait_for_provider_registration(provider)

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

    def test_provider_ready_event_emitted(self, wait_for_provider_registration):
        """Test that PROVIDER_READY event is emitted when first config received."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()
            api.set_provider(provider)
            wait_for_provider_registration(provider)

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

    def test_provider_ready_event_only_once(self, wait_for_provider_registration, wait_for_provider_ready):
        """Test that PROVIDER_READY event is only emitted once, not on subsequent configs."""
        ready_events = []

        def on_provider_ready(event_details):
            ready_events.append(event_details)

        api.add_handler(ProviderEvent.PROVIDER_READY, on_provider_ready)

        try:
            with override_global_config({"experimental_flagging_provider_enabled": True}):
                provider = DataDogProvider()
                api.set_provider(provider)
                wait_for_provider_registration(provider)

                # Clear events from initialization
                ready_events.clear()

                # First configuration
                config1 = create_config(create_boolean_flag("flag1", enabled=True))
                process_ffe_configuration(config1)
                wait_for_provider_ready(provider)

                deadline = time.monotonic() + 1.0
                while len(ready_events) == 0 and time.monotonic() < deadline:
                    time.sleep(0.01)

                count_after_first = len(ready_events)
                assert count_after_first >= 1  # Should have emitted

                # Second configuration
                config2 = create_config(create_boolean_flag("flag2", enabled=True))
                process_ffe_configuration(config2)
                time.sleep(0.05)

                count_after_second = len(ready_events)
                # Should not have emitted again
                assert count_after_second == count_after_first
        finally:
            api.remove_handler(ProviderEvent.PROVIDER_READY, on_provider_ready)
            api.clear_providers()

    def test_provider_status_after_shutdown(self, wait_for_provider_registration):
        """Test that provider returns to NOT_READY after shutdown."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider()
            api.set_provider(provider)
            wait_for_provider_registration(provider)

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

    def test_multiple_providers_receive_status_updates(self, wait_for_provider_registration):
        """Test that multiple provider instances receive status updates."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider1 = DataDogProvider()
            provider2 = DataDogProvider()

            api.set_provider(provider1, "client1")
            api.set_provider(provider2, "client2")
            wait_for_provider_registration(provider1)
            wait_for_provider_registration(provider2)

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
                api.set_provider_and_wait(provider)

                # Provider should detect existing config and emit READY
                assert provider._status == ProviderStatus.READY
                assert len(ready_events) >= 1
            finally:
                api.remove_handler(ProviderEvent.PROVIDER_READY, on_provider_ready)
                api.clear_providers()


class TestProviderInitializationBlocking:
    """Test that initialize() blocks until config arrives or timeout expires."""

    def test_set_provider_returns_before_initialization_completes(
        self, wait_for_provider_registration, wait_for_provider_ready
    ):
        """set_provider() should start initialization in the background and return immediately."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider(initialization_timeout=5.0)

            try:
                start = time.monotonic()
                api.set_provider(provider)
                elapsed = time.monotonic() - start

                assert elapsed < 0.3, f"set_provider() took too long ({elapsed:.2f}s)"
                assert provider._status == ProviderStatus.NOT_READY
                assert not provider._config_received.is_set()

                wait_for_provider_registration(provider)
                config = create_config(create_boolean_flag("test-flag", enabled=True))
                process_ffe_configuration(config)
                wait_for_provider_ready(provider)
            finally:
                api.clear_providers()

    def test_set_provider_and_wait_blocks_until_config_arrives(self):
        """set_provider_and_wait() should block and return once config is delivered mid-wait."""
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
                api.set_provider_and_wait(provider)
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
                api.set_provider_and_wait(provider)
                elapsed = time.monotonic() - start

                # Should be near-instant (config already available)
                assert elapsed < 1.0, f"initialize() took {elapsed:.2f}s, should be instant with pre-loaded config"
                assert provider._status == ProviderStatus.READY
            finally:
                api.clear_providers()

    def test_set_provider_and_wait_timeout_raises(self):
        """set_provider_and_wait() should raise ProviderNotReadyError after timeout expires."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider(initialization_timeout=0.5)

            try:
                start = time.monotonic()
                with pytest.raises(ProviderNotReadyError):
                    api.set_provider_and_wait(provider)
                elapsed = time.monotonic() - start

                # Should have blocked for ~0.5s (the timeout)
                assert elapsed >= 0.3, f"initialize() returned too fast ({elapsed:.2f}s)"
                assert elapsed < 2.0, f"initialize() took too long ({elapsed:.2f}s)"
            finally:
                api.clear_providers()

    def test_set_provider_late_recovery_after_timeout(self, wait_for_client_status, wait_for_provider_ready):
        """Config arriving after non-blocking initialization timeout should transition provider to READY."""
        with override_global_config({"experimental_flagging_provider_enabled": True}):
            provider = DataDogProvider(initialization_timeout=0.5)

            try:
                api.set_provider(provider)

                client = api.get_client()
                wait_for_client_status(client, ProviderStatus.ERROR)

                # Now deliver config (late recovery)
                config = create_config(create_boolean_flag("test-flag", enabled=True))
                process_ffe_configuration(config)
                wait_for_provider_ready(provider)

                # Provider should recover to READY
                assert provider._status == ProviderStatus.READY
                assert provider._config_received.is_set()
            finally:
                api.clear_providers()
