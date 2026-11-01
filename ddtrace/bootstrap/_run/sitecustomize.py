import os
import sys


try:
    import ddtrace  # noqa: F401
except Exception:
    # The launcher previously imported ddtrace before exec, where import errors were fatal.
    import traceback

    traceback.print_exc()
    raise SystemExit(1)

# Delegate to the shared bootstrap, including its handling of user sitecustomize modules.
bootstrap_dir = os.path.dirname(os.path.dirname(__file__))
sys.path[sys.path.index(os.path.dirname(__file__))] = bootstrap_dir
del sys.modules["sitecustomize"]
import sitecustomize  # noqa: E402,F401
