import sys

import ddtrace.appsec.ddwaf
import ddtrace.bootstrap.sitecustomize as module


if __name__ == "__main__":
    ddtrace.appsec.ddwaf.version()

    sys.exit(0 if module.loaded else 1)
