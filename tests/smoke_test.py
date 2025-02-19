import copy
import os
from platform import system
import subprocess
import sys
import textwrap


def mac_supported_iast_version():
    if system() == "Darwin":
        # TODO: MacOS 10.9 or lower has a old GCC version but cibuildwheel has a GCC old version in newest mac versions
        # mac_version = [int(i) for i in mac_ver()[0].split(".")]
        # mac_version > [10, 9]
        return False
    return True


# Code need to be run in a separate subprocess to reload since reloading .so files doesn't
# work like normal Python ones
test_native_load_code = """
import os
import sys
import logging

log_messages = []

class ListHandler(logging.Handler):
    def emit(self, record):
        log_messages.append(record.getMessage())

# the _native module will produce the "IAST not enabled..." message using the root logger (logger.warning)
# so we have to configure the capture handler on the root logger
root_logger = logging.getLogger()
root_logger.addHandler(ListHandler())
root_logger.setLevel(logging.WARNING)

try:
    from ddtrace.appsec._iast._taint_tracking._native import ops

    if os.environ.get("DD_IAST_ENABLED") == "False":
        assert any(
            "IAST not enabled but native module is being loaded" in message
            for message in log_messages
        )
    else:
        assert ops
        assert len(log_messages) == 0
except ImportError as e:
    assert False, "Importing the native module failed, _native probably not compiled correctly: %s" % str(e)
"""

if __name__ == "__main__":
    # ASM IAST smoke test
    if sys.version_info >= (3, 6, 0) and system() != "Windows" and mac_supported_iast_version():
        print("Running native IAST module load test...")
        test_code = textwrap.dedent(test_native_load_code)
        cmd = [sys.executable, "-c", test_code]
        orig_env = os.environ.copy()
        copied_env = copy.deepcopy(orig_env)

        try:
            print("Running native module load test with DD_IAST_ENABLED=False...")
            copied_env["DD_IAST_ENABLED"] = "False"
            result = subprocess.run(cmd, env=copied_env, capture_output=True, text=True)
            assert result.returncode == 0, "Failed with DD_IAST_ENABLED=0: %s, %s" % (result.stdout, result.stderr)

            print("Running native module load test with DD_IAST_ENABLED=True...")
            copied_env["DD_IAST_ENABLED"] = "True"
            result = subprocess.run(cmd, env=copied_env, capture_output=True, text=True)
            assert result.returncode == 0, "Failed with DD_IAST_ENABLED=1: %s, %s" % (result.stdout, result.stderr)
            print("IAST module load tests completed successfully")
        finally:
            os.environ = orig_env

    # ASM WAF smoke test
    if system() != "Linux" or sys.maxsize > 2**32:
        import ddtrace.appsec._ddwaf
        import ddtrace.bootstrap.sitecustomize as module

        print("Running WAF module load test...")
        # Proceed with the WAF module load test
        ddtrace.appsec._ddwaf.version()
        assert ddtrace.appsec._ddwaf._DDWAF_LOADED
        assert module.loaded
        print("WAF module load test completed successfully")
    else:
        # Skip the test for 32-bit Linux systems
        print("Skipping test, 32-bit DDWAF not ready yet")
