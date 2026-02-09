"""Test script to run with ddtrace-run to simulate uvicorn environment."""

print("SCA Detection Test Script Running...")

# Check SCA status after ddtrace has been initialized
from ddtrace.appsec._constants import SCA  # noqa: E402
from ddtrace.appsec.sca import _sca_detection_enabled  # noqa: E402
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller  # noqa: E402
from ddtrace.internal.settings._config import config as tracer_config  # noqa: E402
from ddtrace.internal.settings.asm import config as asm_config  # noqa: E402


print(f"tracer_config._sca_enabled: {tracer_config._sca_enabled}")
print(f"asm_config._sca_detection_enabled: {asm_config._sca_detection_enabled}")
print(f"_sca_detection_enabled: {_sca_detection_enabled}")

registered = remoteconfig_poller.get_registered(SCA.RC_PRODUCT)
print(f"SCA_DETECTION registered with RC: {registered is not None}")

if registered:
    print(f"Registered type: {type(registered)}")
else:
    print("ERROR: SCA_DETECTION not registered!")

# Check products
from ddtrace.internal.products import manager  # noqa: E402


print(f"\nAvailable products: {list(manager._ProductsRegistry__products__.keys())}")

# Check if SCA product exists
sca_product = manager._ProductsRegistry__products__.get("sca")
if sca_product:
    print(f"SCA product found: {sca_product}")
    print(f"SCA product has start: {hasattr(sca_product, 'start')}")
    print(f"SCA product requires: {getattr(sca_product, 'requires', [])}")
else:
    print("ERROR: SCA product not found!")

print("\nTest complete")
