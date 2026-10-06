import bm

from ddtrace.internal import packages
from ddtrace.internal.packages import _package_for_root_module_mapping


class PackagesPackageForRootModuleMapping(bm.Scenario):
    disable_cache: bool

    def run(self):
        def _(loops):
            for _ in range(loops):
                f = _package_for_root_module_mapping
                if self.disable_cache:
                    if hasattr(packages, "_reset_installed_distributions"):
                        # Newer versions cache a snapshot of the installed
                        # distributions that this mapping is derived from.
                        packages._reset_installed_distributions()
                    else:
                        f = _package_for_root_module_mapping.__closure__[0].cell_contents
                result = f()
                # Ensure the result is used
                assert result is not None
                assert len(result) > 0

        yield _
