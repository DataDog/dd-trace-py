import os
import sys

import preload  # noqa


# Check for and import any sitecustomize that would have normally been used
bootstrap_dir = os.path.abspath(os.path.dirname(__file__))
abs_paths = [os.path.abspath(_) for _ in sys.path]
if bootstrap_dir in abs_paths:
    index = abs_paths.index(bootstrap_dir)
    del sys.path[index]

    # NOTE: this reference to the module is crucial in Python 2.
    # Without it the current module gets gc'd and all subsequent references
    # will be `None`.
    our_sitecustomize_module = sys.modules["sitecustomize"]
    del sys.modules["sitecustomize"]
    try:
        import sitecustomize  # noqa
    except ImportError:
        # If an additional sitecustomize is not found then put our
        # sitecustomize back.
        sys.modules["sitecustomize"] = our_sitecustomize_module
    finally:
        sys.path.insert(index, bootstrap_dir)
else:
    try:
        import sitecustomize  # noqa
    except ImportError:
        pass
