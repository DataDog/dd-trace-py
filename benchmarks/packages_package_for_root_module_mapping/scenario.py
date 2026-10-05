import bm

from ddtrace.internal import packages
from ddtrace.internal.packages import _package_for_root_module_mapping


class PackagesPackageForRootModuleMapping(bm.Scenario):
    disable_cache: bool

    def run(self):
        def _(loops):
            for _ in range(loops):
                if self.disable_cache and hasattr(packages, "_reset_installed_distributions"):
                    # Newer versions share one cached distribution scan between
                    # this mapping and the other package maps.
                    packages._reset_installed_distributions()
                f = (
                    _package_for_root_module_mapping.__closure__[0].cell_contents
                    if self.disable_cache
                    else _package_for_root_module_mapping
                )
                result = f()
                # Ensure the result is used
                assert result is not None
                assert len(result) > 0

        yield _
