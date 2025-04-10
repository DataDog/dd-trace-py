import sys

import bm

from ddtrace.internal.packages import get_module_distribution_versions
from ddtrace.internal.packages import parse_importlib_metadata
from ddtrace.internal.telemetry.data import update_imported_dependencies


class PackagesUpdateImportedDependencies(bm.Scenario):
    imported_deps: list[str]
    use_cache: bool
    include_sys_modules: bool

    def clear_caches(self, use_cache: bool = False):
        if not use_cache:
            get_module_distribution_versions.cache_clear()
            if hasattr(parse_importlib_metadata, "cache_clear"):
                parse_importlib_metadata.cache_clear()

    def run(self):
        # Clear any initial caches
        self.clear_caches()

        if self.include_sys_modules:
            self.imported_deps.extend(sys.modules.keys())

        def _(loops):
            for _ in range(loops):
                self.clear_caches(self.use_cache)
                update_imported_dependencies({}, self.imported_deps)

        yield _
