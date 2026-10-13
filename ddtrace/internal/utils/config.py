import os
import sys
from typing import Optional


def get_application_name() -> Optional[str]:
    """Attempts to find the application name using system arguments."""
    try:
        import __main__

        name = __main__.__file__
    except (ImportError, AttributeError):
        try:
            name = sys.argv[0]
        except (AttributeError, IndexError):
            return None

    return os.path.basename(name)
