import os
import sys
from typing import Optional


def get_application_name():
    # type: () -> Optional[str]
    """Attempts to find the application name using system arguments."""
    if hasattr(sys, "argv") and sys.argv[0]:
        app_name = os.path.basename(sys.argv[0])  # type: Optional[str]
    else:
        app_name = None
    return app_name
