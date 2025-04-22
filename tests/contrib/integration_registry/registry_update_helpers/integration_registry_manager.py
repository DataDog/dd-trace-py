import builtins
from collections import defaultdict
import importlib.metadata
import traceback


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
        self.pending_updates = defaultdict(lambda: defaultdict(lambda: {
            "top_level_module": "",
            "version": "",
        }))

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
        # reverse the lines to check the most recent patch call first since some integrations call
        # other integrations patches:
        #   e.g. mongoengine calls pymongo's patch
        return any(
            "ddtrace/contrib/internal" in line and "/patch.py" in line for line in reversed(tb_string.splitlines())
        )

    def _get_integration_name_from_traceback(self, tb_string):
        """Extracts integration name (directory name) from traceback string."""
        for line in reversed(tb_string.splitlines()):
            if "ddtrace/contrib/internal/" in line:
                try:
                    # e.g., ".../ddtrace/contrib/internal/flask/patch.py" -> "flask"
                    parts = line.split("ddtrace/contrib/internal/")[1].split("/")
                    if parts and parts[0]:
                        return parts[0]
                except (IndexError, Exception):
                    continue
        return None

    def _get_full_module_from_object(self, obj):
        """Gets the full module name from the object, e.g., 'google.generativeai' or 'redis'."""
        attrs = ["__module__", "__name__"]
        for attr in attrs:
            try:
                module_name = self.original_getattr(obj, attr)
                # Return if we got a non-empty string
                if isinstance(module_name, str) and module_name:
                    return module_name
            except Exception:
                continue
        return None

    def _distribution_contains_module(self, distribution_name: str, full_module_name: str) -> bool:
        """Checks if a distribution's files contain the specified module."""
        try:
            dist_files = importlib.metadata.files(distribution_name)
            if not dist_files:
                return False

            # Module path components (e.g., ['google', 'generativeai'])
            module_parts = full_module_name.split('.')
            # Possible file paths for the module
            # e.g., google/generativeai.py or google/generativeai/__init__.py
            expected_path_py = "/".join(module_parts) + ".py"
            expected_path_init = "/".join(module_parts) + "/__init__.py"

            for file in dist_files:
                file_path = str(file)
                if file_path.endswith(expected_path_py) or file_path.endswith(expected_path_init):
                    return True
        except Exception:
            return False
        return False

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
                full_module_name = self._get_full_module_from_object(obj)
            except Exception:
                self.processed_objects.add(obj)
                continue

            if full_module_name:
                top_level_module = full_module_name.split('.', 1)[0]
                candidate_distribution_names = pkg_dist_map.get(top_level_module, [])
                integration_name = self._get_integration_name_from_traceback(tb_string)

                if integration_name and candidate_distribution_names:
                    for distribution_name in candidate_distribution_names:
                        # Check if this specific distribution actually contains the patched module
                        if self._distribution_contains_module(distribution_name, full_module_name):
                            update_key = f"{integration_name}:{distribution_name}"
                            if update_key not in self.updated_packages:
                                self.pending_updates[integration_name][distribution_name] = {
                                    "top_level_module": top_level_module,
                                    "version": importlib.metadata.version(distribution_name),
                                }
                                self.updated_packages.add(update_key)

            self.processed_objects.add(obj)

    def patch_getattr(self):
        """Patches builtins.getattr to intercept _datadog_patch access."""
        if self.original_getattr is not None:
            return
        self.original_getattr = builtins.getattr

        def _wrapped_getattr(obj, name, *default):
            og_getattr = self.original_getattr

            if name in ("_datadog_patch", "__datadog_patch"):
                is_processed = obj in self.processed_objects
                is_patched = obj in self.patched_objects

                if not is_processed and not is_patched:
                    tb = traceback.extract_stack()[:-1]
                    tb_string = "".join(traceback.format_list(tb))
                    self.patched_objects[obj] = tb_string

            if default:
                return og_getattr(obj, name, default[0])
            else:
                return og_getattr(obj, name)

        builtins.getattr = _wrapped_getattr

    def cleanup_patch(self):
        """Restores getattr and clears internal state."""
        if self.original_getattr:
            builtins.getattr = self.original_getattr
            self.original_getattr = None

    
    def cleanup_post_session(self):
        self.patched_objects.clear()
        self.processed_objects.clear()
        self.updated_packages.clear()
        self.pending_updates.clear()
        self.packages_distributions = None
        self.cleanup_patch()


registry_manager = IntegrationRegistryManager()
