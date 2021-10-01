import sys

import ddtrace.bootstrap.sitecustomize as module


if __name__ == "__main__":
    sys.exit(0 if module.loaded else 1)
