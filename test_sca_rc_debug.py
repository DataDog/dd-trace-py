"""Debug script to verify SCA Remote Config is working correctly."""

import os


# Set environment variables before importing ddtrace
os.environ["DD_APPSEC_SCA_ENABLED"] = "true"
os.environ["DD_SCA_DETECTION_ENABLED"] = "true"
os.environ["DD_APPSEC_ENABLED"] = "true"
os.environ["DD_REMOTE_CONFIGURATION_ENABLED"] = "true"
os.environ["DD_TRACE_DEBUG"] = "true"  # Enable debug logging

print("=" * 80)
print("SCA Remote Config Debug Test")
print("=" * 80)

# Check environment variables
print("\n1. Environment Variables:")
print(f"   DD_APPSEC_SCA_ENABLED={os.environ.get('DD_APPSEC_SCA_ENABLED')}")
print(f"   DD_SCA_DETECTION_ENABLED={os.environ.get('DD_SCA_DETECTION_ENABLED')}")
print(f"   DD_APPSEC_ENABLED={os.environ.get('DD_APPSEC_ENABLED')}")
print(f"   DD_REMOTE_CONFIGURATION_ENABLED={os.environ.get('DD_REMOTE_CONFIGURATION_ENABLED')}")

# Import ddtrace to trigger product loading
print("\n2. Importing ddtrace...")
import ddtrace  # noqa: E402, F401 - Import after env setup, unused but triggers initialization
from ddtrace.internal.settings._config import config as tracer_config  # noqa: E402
from ddtrace.internal.settings.asm import config as asm_config  # noqa: E402


# Check config values
print("\n3. Configuration Values:")
print(f"   tracer_config._sca_enabled={tracer_config._sca_enabled}")
print(f"   asm_config._sca_detection_enabled={asm_config._sca_detection_enabled}")

# Check if SCA detection is enabled
print("\n4. Checking SCA Detection Status:")
try:
    from ddtrace.appsec.sca import _sca_detection_enabled

    print(f"   SCA detection enabled: {_sca_detection_enabled}")
except ImportError as e:
    print(f"   ERROR importing SCA: {e}")

# Check if RC is registered
print("\n5. Checking Remote Config Registration:")
try:
    from ddtrace.appsec._constants import SCA
    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    registered = remoteconfig_poller.get_registered(SCA.RC_PRODUCT)
    print(f"   SCA_DETECTION registered: {registered is not None}")
    if registered:
        print(f"   Registered object type: {type(registered)}")
except Exception as e:
    print(f"   ERROR checking registration: {e}")

# Check registry
print("\n6. Checking SCA Registry:")
try:
    from ddtrace.appsec.sca._registry import get_global_registry

    registry = get_global_registry()
    print(f"   Registry created: {registry is not None}")
    print(f"   Registry type: {type(registry)}")
except Exception as e:
    print(f"   ERROR checking registry: {e}")

# Try to manually trigger RC callback
print("\n7. Testing RC Callback Manually:")
try:
    from ddtrace.appsec.sca._remote_config import _sca_detection_callback

    class MockPayload:
        def __init__(self, targets):
            self.path = "datadog/2/SCA_DETECTION/test/config"
            self.content = {"targets": targets}
            self.metadata = {}

    print("   Creating mock payload with target: os.path:join")
    payload = MockPayload(["os.path:join"])

    print("   Calling _sca_detection_callback...")
    _sca_detection_callback([payload])

    print("   Callback executed successfully")

    # Check if target was added to registry
    from ddtrace.appsec.sca._registry import get_global_registry

    registry = get_global_registry()
    print(f"   Target in registry: {registry.has_target('os.path:join')}")
    print(f"   Target instrumented: {registry.is_instrumented('os.path:join')}")

except Exception as e:
    print(f"   ERROR testing callback: {e}")
    import traceback

    traceback.print_exc()

print("\n" + "=" * 80)
print("Debug test complete")
print("=" * 80)
