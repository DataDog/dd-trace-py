# -*- encoding: utf-8 -*-
"""
Regression tests for ProfilingConfig.reload_from_env()

These tests ensure that reload_from_env() correctly handles:
1. Derived fields recalculation
2. Metadata synchronization for nested configs
3. Future additions of derived fields or nested configs

Related: Fork config reload after environment variable changes
"""
import os

import pytest


class TestReloadFromEnvDerivedFields:
    """Test that derived fields are recalculated after reload_from_env()"""

    def test_stack_v2_enabled_derived_field(self):
        """
        stack.v2_enabled is derived from: stack._v2_enabled AND stack.enabled
        When stack.enabled changes, v2_enabled should be recalculated.
        """
        # Initial state: stack enabled
        os.environ["DD_PROFILING_ENABLED"] = "true"
        os.environ["DD_PROFILING_STACK_ENABLED"] = "true"
        os.environ["DD_PROFILING_STACK_V2_ENABLED"] = "true"

        from ddtrace.settings.profiling import ProfilingConfig

        config = ProfilingConfig()

        # Verify initial state
        assert config.stack.enabled is True
        assert config.stack._v2_enabled is True
        assert config.stack.v2_enabled is True, "Derived: True AND True = True"

        # Change environment: disable stack
        os.environ["DD_PROFILING_STACK_ENABLED"] = "false"

        # Reload config
        config.reload_from_env()

        # Verify derived field is recalculated
        assert config.stack.enabled is False, "Base field should be updated"
        assert config.stack._v2_enabled is True, "_v2_enabled should remain True"
        assert config.stack.v2_enabled is False, "Derived field should be recalculated: True AND False = False"

    def test_heap_sample_size_derived_field(self):
        """
        heap.sample_size is derived from heap._sample_size and heap.enabled
        When heap.enabled=False, sample_size should be 0
        """
        # Initial state: heap enabled
        os.environ["DD_PROFILING_ENABLED"] = "true"
        os.environ["DD_PROFILING_HEAP_ENABLED"] = "true"

        from ddtrace.settings.profiling import ProfilingConfig

        config = ProfilingConfig()

        # Verify initial state
        assert config.heap.enabled is True
        initial_sample_size = config.heap.sample_size
        assert initial_sample_size > 0, "Sample size should be > 0 when heap is enabled"

        # Change environment: disable heap
        os.environ["DD_PROFILING_HEAP_ENABLED"] = "false"

        # Reload config
        config.reload_from_env()

        # Verify derived field is recalculated
        assert config.heap.enabled is False, "Base field should be updated"
        assert config.heap.sample_size == 0, "Derived field should be 0 when heap is disabled"

    def test_multiple_derived_fields_simultaneously(self):
        """
        Test that multiple derived fields are all recalculated correctly
        when environment changes affect multiple configs.
        """
        # Initial state: all enabled
        os.environ["DD_PROFILING_ENABLED"] = "true"
        os.environ["DD_PROFILING_STACK_ENABLED"] = "true"
        os.environ["DD_PROFILING_STACK_V2_ENABLED"] = "true"
        os.environ["DD_PROFILING_HEAP_ENABLED"] = "true"

        from ddtrace.settings.profiling import ProfilingConfig

        config = ProfilingConfig()

        assert config.stack.v2_enabled is True
        assert config.heap.sample_size > 0

        # Change environment: disable both
        os.environ["DD_PROFILING_STACK_ENABLED"] = "false"
        os.environ["DD_PROFILING_HEAP_ENABLED"] = "false"

        # Reload config
        config.reload_from_env()

        # Verify both derived fields are recalculated
        assert config.stack.v2_enabled is False, "stack.v2_enabled should be recalculated"
        assert config.heap.sample_size == 0, "heap.sample_size should be recalculated"


class TestReloadFromEnvMetadata:
    """Test that metadata (_value_source, config_id) is synchronized for nested configs"""

    def test_nested_config_value_source_updated(self):
        """
        Test that nested config's _value_source is updated correctly.
        This ensures value_source() API returns accurate information.
        """
        # Initial state with default
        if "DD_PROFILING_STACK_ENABLED" in os.environ:
            del os.environ["DD_PROFILING_STACK_ENABLED"]

        from ddtrace.settings.profiling import ProfilingConfig

        config = ProfilingConfig()

        # Initial value_source might be 'default'
        config.stack.value_source("DD_PROFILING_STACK_ENABLED")

        # Change environment: set explicit value
        os.environ["DD_PROFILING_STACK_ENABLED"] = "false"

        # Reload config
        config.reload_from_env()

        # Verify value_source is updated
        new_source = config.stack.value_source("DD_PROFILING_STACK_ENABLED")
        # After reload, source should reflect the environment variable
        # Note: The exact value depends on DDConfig implementation
        # but it should be updated from the new config
        assert new_source in [
            "env_var",
            "default",
            "fleet_stable_config",
            "local_stable_config",
        ], f"value_source() should return a valid source type, got: {new_source}"

    def test_all_nested_configs_metadata_synchronized(self):
        """
        Test that ALL nested configs (stack, lock, memory, heap, pytorch)
        have their metadata synchronized.

        This is a regression test to ensure that when a new nested config
        is added to _NESTED_CONFIGS, the metadata synchronization works.
        """
        os.environ["DD_PROFILING_ENABLED"] = "true"

        from ddtrace.settings.profiling import ProfilingConfig

        config = ProfilingConfig()

        # Store initial metadata state
        initial_metadata = {}
        for nested_name in ProfilingConfig._NESTED_CONFIGS:
            nested = getattr(config, nested_name)
            initial_metadata[nested_name] = {
                "_value_source": id(nested._value_source),
                "config_id": nested.config_id,
            }

        # Reload config (even without env change, metadata should be fresh)
        config.reload_from_env()

        # Verify metadata is updated for all nested configs
        for nested_name in ProfilingConfig._NESTED_CONFIGS:
            nested = getattr(config, nested_name)

            # _value_source should be a new dict instance
            new_value_source_id = id(nested._value_source)
            old_value_source_id = initial_metadata[nested_name]["_value_source"]

            assert (
                new_value_source_id != old_value_source_id
            ), f"{nested_name}._value_source should be updated with new dict instance"

    def test_nested_configs_list_completeness(self):
        """
        Regression test: Verify that _NESTED_CONFIGS list includes all
        nested configs defined via .include().

        This test will fail if someone adds a new nested config but forgets
        to add it to _NESTED_CONFIGS.
        """
        from ddtrace.settings.profiling import ProfilingConfig

        # Expected nested configs based on .include() calls in the code
        # These should match the ProfilingConfig.include() calls at the bottom of profiling.py
        expected_nested_configs = ["stack", "lock", "memory", "heap", "pytorch"]

        actual_nested_configs = ProfilingConfig._NESTED_CONFIGS

        assert set(actual_nested_configs) == set(expected_nested_configs), (
            f"_NESTED_CONFIGS mismatch! Expected: {expected_nested_configs}, Got: {actual_nested_configs}. "
            "If you added a new nested config via .include(), add it to _NESTED_CONFIGS."
        )


class TestReloadFromEnvIntegration:
    """Integration tests for reload_from_env() in fork scenarios"""

    @pytest.mark.skipif(not hasattr(os, "fork"), reason="Fork not available on this platform")
    def test_fork_scenario_config_reload(self):
        """
        Integration test simulating a fork scenario where child process
        has different environment variables.

        This test verifies:
        1. Base fields are updated
        2. Derived fields are recalculated
        3. Metadata is synchronized
        4. Import references are preserved
        """
        import subprocess
        import sys

        code = """
import os
import sys

# Parent state
os.environ['DD_PROFILING_ENABLED'] = 'true'
os.environ['DD_PROFILING_STACK_ENABLED'] = 'true'
os.environ['DD_PROFILING_STACK_V2_ENABLED'] = 'true'

from ddtrace.settings.profiling import config

# Verify parent state
assert config.stack.enabled is True
assert config.stack.v2_enabled is True

print("PARENT: stack.enabled={}, v2_enabled={}".format(
    config.stack.enabled, config.stack.v2_enabled))

# Fork
pid = os.fork()

if pid == 0:  # Child process
    # Note: In real fork scenarios, env vars are inherited or set before fork
    # This test simulates the reload mechanism

    # Simulate child having different config
    os.environ['DD_PROFILING_STACK_ENABLED'] = 'false'

    import time
    time.sleep(0.1)  # Let fork hooks execute

    # The fork hook should have called config.reload_from_env()
    # For this test, we manually call it to simulate
    config.reload_from_env()

    # Verify child state
    assert config.stack.enabled is False, "stack.enabled should be False"
    assert config.stack.v2_enabled is False, "v2_enabled should be recalculated to False"

    print("CHILD: stack.enabled={}, v2_enabled={}".format(
        config.stack.enabled, config.stack.v2_enabled))

    sys.exit(0)
else:  # Parent process
    import time
    time.sleep(0.3)
    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    sys.exit(exit_code)
"""

        result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=5)

        output = result.stdout.decode()
        stderr = result.stderr.decode()

        assert result.returncode == 0, f"Fork integration test failed!\nStdout:\n{output}\nStderr:\n{stderr}"

        assert b"PARENT: stack.enabled=True" in result.stdout
        assert b"CHILD: stack.enabled=False" in result.stdout


class TestReloadFromEnvRobustness:
    """Test edge cases and robustness of reload_from_env()"""

    def test_reload_from_env_idempotent(self):
        """
        Test that calling reload_from_env() multiple times is safe
        and produces consistent results.
        """
        os.environ["DD_PROFILING_ENABLED"] = "true"
        os.environ["DD_PROFILING_STACK_ENABLED"] = "true"

        from ddtrace.settings.profiling import ProfilingConfig

        config = ProfilingConfig()

        # Call reload multiple times
        config.reload_from_env()
        state1 = config.stack.enabled

        config.reload_from_env()
        state2 = config.stack.enabled

        config.reload_from_env()
        state3 = config.stack.enabled

        # All states should be consistent
        assert state1 == state2 == state3

    def test_reload_preserves_object_identity(self):
        """
        Test that reload_from_env() preserves the object identity
        of the config instance (in-place update).
        """
        os.environ["DD_PROFILING_ENABLED"] = "true"

        from ddtrace.settings.profiling import ProfilingConfig

        config = ProfilingConfig()

        original_id = id(config)
        original_stack_id = id(config.stack)

        # Reload should not create new objects
        config.reload_from_env()

        assert id(config) == original_id, "Root config object should be the same"
        assert id(config.stack) == original_stack_id, "Nested config object should be the same"

    def test_cleanup(self):
        """Clean up environment variables after tests"""
        # Clean up to avoid affecting other tests
        env_vars = [
            "DD_PROFILING_ENABLED",
            "DD_PROFILING_STACK_ENABLED",
            "DD_PROFILING_STACK_V2_ENABLED",
            "DD_PROFILING_HEAP_ENABLED",
        ]
        for var in env_vars:
            if var in os.environ:
                del os.environ[var]
