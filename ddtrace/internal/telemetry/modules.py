import sys
from typing import Set


def get_newly_imported_modules(already_imported: Set[str]) -> Set[str]:
    latest_modules = set(sys.modules.keys())
    new_modules = latest_modules - already_imported
    already_imported.update(latest_modules)
    return new_modules
