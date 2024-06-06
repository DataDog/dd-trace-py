import os
from platform import system
import sys

print("Loading _ddwaf")
import ddtrace.appsec._ddwaf
print("Loaded _ddwaf")
print("Loading sitecustomize")
import ddtrace.bootstrap.sitecustomize as module
print("Loaded sitecustomize")


def mac_supported_iast_version():
    if system() == "Darwin":
        # TODO: MacOS 10.9 or lower has a old GCC version but cibuildwheel has a GCC old version in newest mac versions
        # mac_version = [int(i) for i in mac_ver()[0].split(".")]
        # mac_version > [10, 9]
        return False
    return True


if __name__ == "__main__":
    # ASM IAST smoke test
    if (3, 6, 0) <= sys.version_info < (3, 12) and system() != "Windows" and mac_supported_iast_version():
        # ASM IAST import error test
        import_error = False
        try:
            print("Importing _native.ops")
            from ddtrace.appsec._iast._taint_tracking._native import ops
            print("Imported _native.ops")
        except ImportError:
            import_error = True

        assert import_error
        assert "ddtrace.appsec._iast._taint_tracking._native.ops" not in sys.modules

        os.environ["DD_IAST_ENABLED"] = "True"

        print("Importing _native.opsi again")
        from ddtrace.appsec._iast._taint_tracking._native import ops
        print("Imported _native.opsi again")

        assert ops

    # ASM WAF smoke test
    if system() == "Linux":
        if not sys.maxsize > 2**32:
            # 32-bit linux DDWAF not ready yet.
            sys.exit(0)

    print("Getting version")
    ddtrace.appsec._ddwaf.version()
    print("Got version")
    print("Checking load status")
    assert ddtrace.appsec._ddwaf._DDWAF_LOADED
    print("Checked load status")
    assert module.loaded
