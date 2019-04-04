from __future__ import print_function

import ddtrace.bootstrap.sitecustomize as module


if __name__ == '__main__':
    print(module.loaded)
