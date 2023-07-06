from platform import system
import sys

import ddtrace.appsec.ddwaf
import ddtrace.bootstrap.sitecustomize as module


if __name__ == "__main__":
    # ASM IAST smoke test
    if sys.version_info >= (3, 6, 0) and system() != "Windows":
        from ddtrace.appsec.iast._taint_tracking._native import ops

        assert ops

    # ASM WAF smoke test
    if system() == "Linux":
        if not sys.maxsize > 2 ** 32:
            # 32-bit linux DDWAF not ready yet.
            sys.exit(0)

    ddtrace.appsec.ddwaf.version()
    assert ddtrace.appsec.ddwaf._DDWAF_LOADED
    assert module.loaded
