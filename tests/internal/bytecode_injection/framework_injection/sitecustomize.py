from pathlib import Path
import sys

import preload  # noqa:E402,F401


# Check for and import any sitecustomize that would have normally been used
bootstrap_dir = str(Path(__file__).parent.resolve())
abs_paths = [str(Path(_).resolve()) for _ in sys.path]
if bootstrap_dir in abs_paths:
    index = abs_paths.index(bootstrap_dir)
    del sys.path[index]

    our_sitecustomize_module = sys.modules["sitecustomize"]
    del sys.modules["sitecustomize"]
    try:
        import sitecustomize  # noqa:F401
    except ImportError:
        # If an additional sitecustomize is not found then put our
        # sitecustomize back.
        sys.modules["sitecustomize"] = our_sitecustomize_module
    finally:
        sys.path.insert(index, bootstrap_dir)
else:
    try:
        import sitecustomize  # noqa:F401
    except ImportError:
        pass
