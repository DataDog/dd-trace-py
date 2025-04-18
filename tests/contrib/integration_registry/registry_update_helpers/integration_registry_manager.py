import builtins
import importlib.metadata
import traceback
from collections import defaultdict


class IntegrationRegistryManager:
    """
    Watches for _datadog_patch attribute access on modules via getattr patching.
    Collects integration and distribution names based on traceback and metadata.
    Provides the collected data for external processing.
    """
    def __init__(self):
        self.updated_packages = set()
        self.packages_distributions = None
        self.patched_objects = {}
        self.processed_objects = set()
        self.original_getattr = None
        self.pending_updates = defaultdict(lambda: {"dependency_name": set()})

    def get_cached_packages_distributions(self):
        """Gets package->distribution mapping, caching the result."""
        if self.packages_distributions is None:
            try:
                self.packages_distributions = importlib.metadata.packages_distributions()
            except AttributeError:
                try:
                    import importlib_metadata
                    self.packages_distributions = importlib_metadata.packages_distributions()
                except Exception:
                    self.packages_distributions = {}
            except Exception:
                self.packages_distributions = {}
        return self.packages_distributions

    def _is_valid_patch_call(self, tb_string):
        """Checks if the patch call originated from ddtrace.contrib.internal/*/patch.py."""
        return any(
            "ddtrace/contrib/internal" in line and "/patch.py" in line
            for line in tb_string.splitlines()
        )

    def _get_integration_name_from_traceback(self, tb_string):
        """Extracts integration name (directory name) from traceback string."""
        for line in tb_string.splitlines():
            if "ddtrace/contrib/internal/" in line:
                try:
                    # e.g., ".../ddtrace/contrib/internal/flask/patch.py" -> "flask"
                    parts = line.split("ddtrace/contrib/internal/")[1].split("/")
                    if parts and parts[0]:
                        return parts[0]
                except (IndexError, Exception):
                    continue
        return None

    def process_patched_objects(self):
        """
        Processes objects recorded via getattr patching.
        Identifies integration/distribution pairs and accumulates them for export.
        """
        self.pending_updates.clear()
        pkg_dist_map = self.get_cached_packages_distributions()

        for obj, tb_string in list(self.patched_objects.items()):
            if obj in self.processed_objects or not self._is_valid_patch_call(tb_string):
                continue

            try:
                obj_name = self.original_getattr(obj, '__module__')
                top_level_module = obj_name.split('.', 1)[0]
            except Exception:
                self.processed_objects.add(obj)
                continue

            if top_level_module:
                distribution_names = pkg_dist_map.get(top_level_module, [])
                integration_name = self._get_integration_name_from_traceback(tb_string)

                if integration_name and distribution_names:
                    for distribution_name in distribution_names:
                        update_key = f"{integration_name}:{distribution_name}"
                        if update_key not in self.updated_packages:
                            self.pending_updates[integration_name]["dependency_name"].add(distribution_name.lower())
                            self.updated_packages.add(update_key)

            self.processed_objects.add(obj)

        for data in self.pending_updates.values():
            if "dependency_name" in data:
                data["dependency_name"] = sorted(list(data["dependency_name"]))

    def get_processed_data_for_export(self) -> dict:
        """Returns the processed update data."""
        return dict(self.pending_updates)

    def patch_getattr(self):
        """Patches builtins.getattr to intercept _datadog_patch access."""
        if self.original_getattr is not None:
             return
        self.original_getattr = builtins.getattr

        def _wrapped_getattr(obj, name, *default):
            og_getattr = self.original_getattr

            if name in ("_datadog_patch", "__datadog_patch"):
                try:
                    is_processed = obj in self.processed_objects
                    is_patched = obj in self.patched_objects
                except TypeError:
                    is_processed = False
                    is_patched = False

                if not is_processed and not is_patched:
                    tb = traceback.extract_stack()[:-1]
                    tb_string = ''.join(traceback.format_list(tb))
                    self.patched_objects[obj] = tb_string

            if default:
                return og_getattr(obj, name, default[0])
            else:
                return og_getattr(obj, name)

        builtins.getattr = _wrapped_getattr

    def cleanup(self):
        """Restores getattr and clears internal state."""
        if self.original_getattr:
            builtins.getattr = self.original_getattr
            self.original_getattr = None
        self.patched_objects.clear()
        self.processed_objects.clear()
        self.updated_packages.clear()
        self.pending_updates.clear()
        self.packages_distributions = None


registry_manager = IntegrationRegistryManager()
