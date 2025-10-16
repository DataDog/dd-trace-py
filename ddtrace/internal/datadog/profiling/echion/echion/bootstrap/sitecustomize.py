# This file is part of "echion" which is released under MIT.
#
# Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

import os
import sys


# Keep the atexit module to preserve the registered hooks.
LOADED_MODULES = frozenset(set(sys.modules.keys()) | {"sitecustomize", "atexit"})


# Make sure to unload all that was loaded during the preload phase, but only
# when necessary.
from importlib.util import find_spec  # noqa

import preload  # noqa


for module in frozenset(["gevent"]):
    if getattr(find_spec(module), "loader", None) is not None:
        for module in set(sys.modules.keys()) - LOADED_MODULES:
            del sys.modules[module]
        break

sys.modules["echion.bootstrap.sitecustomize"] = sys.modules.pop("sitecustomize")

# Check for and import any sitecustomize that would have normally been used
bootstrap_dir = os.path.abspath(os.path.dirname(__file__))
abs_paths = [os.path.abspath(_) for _ in sys.path]
if bootstrap_dir in abs_paths:
    index = abs_paths.index(bootstrap_dir)
    del sys.path[index]

    try:
        import sitecustomize  # noqa
    except ImportError:
        sys.modules["sitecustomize"] = sys.modules["echion.bootstrap.sitecustomize"]
    finally:
        sys.path.insert(index, bootstrap_dir)
else:
    try:
        import sitecustomize  # noqa
    except ImportError:
        sys.modules["sitecustomize"] = sys.modules["echion.bootstrap.sitecustomize"]
