import bm

from ddtrace.internal.packages import _package_for_root_module_mapping


class PackagesPackageForRootModuleMapping(bm.Scenario):
    disable_cache: bool

    def run(self):
        def _(loops):
            for _ in range(loops):
                f = (
                    # This can simply be
                    # _package_for_root_module_mapping.__wrapped__ once this
                    # PR is merged.
                    _package_for_root_module_mapping.__closure__[0].cell_contents
                    if self.disable_cache
                    else _package_for_root_module_mapping
                )
                result = f()
                # Ensure the result is used
                assert result is not None
                assert len(result) > 0

        yield _
