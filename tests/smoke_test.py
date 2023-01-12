import sys

import ddtrace.appsec.ddwaf
import ddtrace.bootstrap.sitecustomize as module


if __name__ == "__main__":
    ddtrace.appsec.ddwaf.version()

    ddwaf_loaded = ddtrace.appsec.ddwaf._DDWAF_LOADED
    sys.exit(0 if module.loaded and ddwaf_loaded else 1)
