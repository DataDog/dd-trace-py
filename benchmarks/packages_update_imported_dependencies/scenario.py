import sys

import bm

from ddtrace.internal.packages import get_module_distribution_versions
from ddtrace.internal.telemetry.data import update_imported_dependencies


class PackagesUpdateImportedDependencies(bm.Scenario):
    imported_deps: list[str]
    use_cache: bool
    include_sys_modules: bool

    def clear_caches(self, use_cache: bool = False):
        if not use_cache:
            get_module_distribution_versions.cache_clear()
            try:
                from ddtrace.internal.packages import get_package_distributions

                if hasattr(get_package_distributions, "cache_clear"):
                    get_package_distributions.cache_clear()
                elif hasattr(get_package_distributions, "__callonce_result__"):
                    del get_package_distributions.__callonce_result__
            except ImportError:
                pass

            try:
                from ddtrace.internal.packages import _DISTRIBUTIONS

                # This will force the next call to parse_importlib_metadata to re-parse the metadata
                _DISTRIBUTIONS = None  # noqa: F811
            except ImportError:
                pass

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
