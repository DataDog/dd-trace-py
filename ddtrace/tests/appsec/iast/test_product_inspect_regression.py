"""
Regression test for inspect module drop causing pytest collection errors.

This test verifies that the IAST product module does NOT drop the inspect module,
as doing so breaks pytest's ability to introspect function signatures during test collection.

Issue: When IAST's post_preload() drops the inspect module, pytest's collection
phase fails with errors like:
    "unexpected object <Signature (...)> in __signature__ attribute"

This happens because:
1. pytest imports inspect and creates Signature objects
2. IAST drops inspect from sys.modules
3. pytest tries to use the old Signature objects with a fresh inspect module
4. The mismatch causes type/identity checks to fail
"""

import sys

from tests.utils import override_env


def sample_function_with_many_params(
    self,
    mfa_enabled,
    totp_client_response,
    client,
    mock_terry_client,
    session_user,
):
    """Function similar to the one that failed in the bug report."""
    pass


class TestInspectModuleDropRegression:
    """Test that IAST does not drop inspect module."""

    def test_simulated_inspect_drop_breaks_pytest_collection(self):
        """
        This test demonstrates the bug by manually simulating what happens.

        It shows that when inspect is dropped and reimported, old Signature
        objects become incompatible with the new inspect module - exactly
        what causes pytest collection to fail.
        """
        import inspect as original_inspect

        # Step 1: pytest creates signatures during collection
        original_sig = original_inspect.signature(sample_function_with_many_params)

        # Step 2: Manually simulate what _drop_safe("inspect") does
        del sys.modules["inspect"]

        # Step 3: Force reimport (happens when pytest continues to use inspect)
        import inspect as new_inspect

        # Step 4: Verify inspect was actually reimported (different module object)
        assert new_inspect is not original_inspect, "Test setup error: inspect should be reimported"

        # Step 5: The bug - isinstance() check fails with mismatched modules
        # This is exactly what breaks pytest's _pytest._code.code module
        is_valid_signature = isinstance(original_sig, new_inspect.Signature)

        # With the bug present, this would be False, breaking pytest
        assert not is_valid_signature, (
            f"Expected isinstance check to fail after inspect module drop. "
            f"Signature type: {type(original_sig)}, "
            f"Expected type: {new_inspect.Signature}"
        )

    def test_iast_post_preload_does_not_drop_inspect(self):
        """
        This is the actual regression test for the fix.

        It verifies that when IAST's post_preload() is called,
        it does NOT drop the inspect module from sys.modules.

        If this test fails, it means _drop_safe("inspect") is being called
        in post_preload(), which will break pytest.
        """
        import inspect  # noqa: F401 - imported to ensure it's in sys.modules before test

        # Capture the current state before IAST initialization
        inspect_id_before = id(sys.modules.get("inspect"))
        inspect_module_before = sys.modules.get("inspect")

        # Call post_preload directly (this is what happens in production)
        # We need to ensure IAST is enabled for post_preload to run its logic
        with override_env({"DD_IAST_ENABLED": "true"}):
            # Force reload of asm_config to pick up the environment variable
            import importlib

            from ddtrace.internal.settings import asm

            importlib.reload(asm)

            from ddtrace.internal.iast import product

            # Call post_preload - this should NOT drop inspect
            product.post_preload()

            # Verify inspect is still in sys.modules
            assert "inspect" in sys.modules, (
                "CRITICAL: inspect module was removed from sys.modules! "
                "This means _drop_safe('inspect') is being called in post_preload(), "
                "which breaks pytest test collection."
            )

            # Verify it's the SAME inspect module (not dropped and reimported)
            inspect_id_after = id(sys.modules.get("inspect"))
            assert inspect_id_before == inspect_id_after, (
                f"CRITICAL: inspect module was dropped and reimported! "
                f"Module ID changed from {inspect_id_before} to {inspect_id_after}. "
                f"This means _drop_safe('inspect') is being called, breaking pytest."
            )

            # Double-check by comparing module objects directly
            inspect_module_after = sys.modules.get("inspect")
            assert inspect_module_before is inspect_module_after, (
                "CRITICAL: inspect module identity changed after post_preload(). "
                "This indicates _drop_safe('inspect') is being called."
            )

    def test_inspect_functionality_after_manual_drop(self):
        """
        Additional test showing inspect operations work after manual drop.

        This confirms the module can be reimported, but signatures created
        with the old module become incompatible.
        """
        import inspect as original_inspect

        # Create signature before drop
        sig_before = original_inspect.signature(sample_function_with_many_params)

        # Drop and reimport
        if "inspect" in sys.modules:
            del sys.modules["inspect"]

        import inspect as new_inspect

        # Create new signature after reimport
        sig_after = new_inspect.signature(sample_function_with_many_params)

        # Both signatures work individually
        assert len(sig_before.parameters) == 6
        assert len(sig_after.parameters) == 6

        # But they're incompatible due to different module versions
        assert type(sig_before) is not type(sig_after)
        assert not isinstance(sig_before, new_inspect.Signature)
        assert isinstance(sig_after, new_inspect.Signature)
