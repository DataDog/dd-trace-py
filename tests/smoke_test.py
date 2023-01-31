from platform import system
import sys

import ddtrace.appsec.ddwaf
import ddtrace.bootstrap.sitecustomize as module


if __name__ == "__main__":
    if system() == "Linux":
        if not sys.maxsize > 2 ** 32:
            # 32-bit linux DDWAF not ready yet.
            sys.exit(0)
    ddtrace.appsec.ddwaf.version()

    assert ddtrace.appsec.ddwaf._DDWAF_LOADED
    assert module.loaded
