import bm

from ddtrace.internal.packages import _package_for_root_module_mapping


class PackagesPackageForRootModuleMapping(bm.Scenario):
    disable_cache: bool

    def run(self):
        def _(loops):
            for _ in range(loops):
                f = _package_for_root_module_mapping
                if self.disable_cache:
                    try:
                        f = _package_for_root_module_mapping.__wrapped__
                    except Exception:
                        try:
                            from ddtrace.internal.packages import _ROOT_TO_PACKAGE
                            _ROOT_TO_PACKAGE = None # noqa: F811
                        except Exception:
                            pass

                result = f()
                # Ensure the result is used
                assert result is not None
                assert len(result) > 0

        yield _
