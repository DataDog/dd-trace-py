import sys
from typing import Set


ALL_MODULES: Set[str] = set()  # All modules that have been already imported

JJJFIRST = True

def get_newly_imported_modules() -> Set[str]:
    global ALL_MODULES
    latest_modules = set(sys.modules.keys())
    new_modules = latest_modules - ALL_MODULES
    ALL_MODULES = latest_modules
    if JJJFIRST:
        import logging;
        LOG = logging.getLogger(__name__)
        LOG.warning("JJJDEBUG get_newly_imported_FIRST: new_modules=%s", new_modules)

    return new_modules
