import builtins
import importlib.metadata
import pathlib
import subprocess
import sys
import traceback
import yaml
from threading import Lock

import pytest


class IntegrationRegistryManager:
    """
    Manages the integration registry YAML file. Watches for objects patched by ddtrace using
    the `_datadog_patch` attribute. If the patched object was determined to be patched from
    a contrib module, it will check the registry YAML file for the integration and distribution
    and update the registry YAML file to include a new integration and/or distribution.
    """
    def __init__(self):
        self.project_root = pathlib.Path(__file__).parent.parent.parent.resolve()
        self.registry_yaml_path = self.project_root / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml"
        self.update_script = self.project_root / "scripts" / "integration_registry" / "update_and_format_registry.py"
        self.yaml_lock = Lock()
        self.yaml_cache = None
        self.updated_packages = set()
        self.packages_distributions = None
        self.patched_objects = {}
        self.processed_objects = set()
        self.original_getattr = None
        self.yaml_was_updated = False

    def get_cached_packages_distributions(self):
        """Gets the mapping, caching it after the first call."""
        if self.packages_distributions is None:
            try:
                self.packages_distributions = importlib.metadata.packages_distributions()
            except Exception:
                self.packages_distributions = {}
        return self.packages_distributions

    def is_valid_patch_call(self, tb_string):
        """Check if the traceback indicates the patch call came from ddtrace.contrib.internal."""
        return any(
            "ddtrace/contrib/internal" in line and "/patch.py" in line
            for line in tb_string.splitlines()
        )

    def get_integration_name_from_traceback(self, tb_string):
        """Extract integration name from traceback string."""
        for line in tb_string.splitlines():
            if "ddtrace/contrib/internal/" in line:
                try:
                    # Extract integration name from path like:
                    #   ".../ddtrace/contrib/internal/INTEGRATION_NAME/patch.py"
                    parts = line.split("ddtrace/contrib/internal/")[1].split("/")
                    if parts and parts[0]:
                        return parts[0]
                except (IndexError, Exception):
                    continue
        return None

    def open_registry_yaml(self):
        try:
            with open(self.registry_yaml_path, "r") as f:
                self.yaml_cache = yaml.safe_load(f)
                return self.yaml_cache
        except Exception:
            return None

    def update_registry_yaml(self, integration_name, distribution_name, update_key):
        integration_found = False
        for entry in self.yaml_cache.get("integrations", []):
            if entry.get("integration_name") == integration_name:
                if entry.get("is_external_package"):
                    deps = entry.get("dependency_name", [])
                    if not isinstance(deps, list): deps = []
                    if distribution_name not in deps:
                        deps.append(distribution_name)
                        entry["dependency_name"] = sorted(list(set(deps)))
                        self.updated_packages.add(update_key)
                        self.yaml_was_updated = True

                        try:
                            with open(self.registry_yaml_path, "w") as f:
                                yaml.dump(self.yaml_cache, f, default_flow_style=False, sort_keys=False, indent=2, width=100)
                        except Exception:
                            pass
                integration_found = True
                break
        if not integration_found:
            self.yaml_cache.get("integrations", []).append({
                "integration_name": integration_name,
                "is_external_package": True,
                "dependency_name": [distribution_name],
            })
            self.updated_packages.add(update_key)
            self.yaml_was_updated = True
        return self.yaml_was_updated
    
    def run_integration_registry_update_script(self):
        result = subprocess.run(
            [sys.executable, str(self.update_script)],
            check=True,
            capture_output=True,
            text=True,
            cwd=self.project_root
        )
        print(result.stdout)
        if result.stderr:
            print(f"Warning: Error running format and update script: {result.stderr}", file=sys.stderr)

    def process_patched_objects(self):
        """Process all objects that had _datadog_patch attribute set during testing."""
        for obj, tb_string in self.patched_objects.items():
            if obj in self.processed_objects:
                continue
                
            if not self.is_valid_patch_call(tb_string):
                continue

            try:
                obj_name = self.original_getattr(obj, '__name__')
                top_level_module = obj_name.split('.', 1)[0]
            except (AttributeError, Exception):
                continue

            distribution_name = None
            if top_level_module:
                pkg_dist_map = self.get_cached_packages_distributions()
                distribution_names = pkg_dist_map.get(top_level_module, [])
                if distribution_names:
                    distribution_name = distribution_names[0]

            if top_level_module and distribution_name:
                integration_name = self.get_integration_name_from_traceback(tb_string)
                if integration_name:
                    update_key = f"{integration_name}:{distribution_name}"
                    # ensure that we only update the registry once per integration/distribution pair
                    if update_key not in self.updated_packages:
                        with self.yaml_lock:
                            if self.yaml_cache is None:
                                self.yaml_cache = self.open_registry_yaml()

                            self.yaml_was_updated = self.update_registry_yaml(integration_name, distribution_name, update_key)
            
            self.processed_objects.add(obj)

        if self.yaml_was_updated:
            self.run_integration_registry_update_script()

    def patch_getattr(self):
        self.original_getattr = builtins.getattr

        def _wrapped_getattr(obj, name, *default):
            # Track any objects that we are patching
            if name in ("_datadog_patch", "__datadog_patch"):
                tb = traceback.extract_stack()[:-1]
                tb_string = ''.join(traceback.format_list(tb))
                if obj not in self.patched_objects:
                    self.patched_objects[obj] = tb_string

            if default:
                return self.original_getattr(obj, name, default[0])
            else:
                return self.original_getattr(obj, name)

        builtins.getattr = _wrapped_getattr

    def cleanup(self):
        """Clean up all state."""
        self.patched_objects.clear()
        self.processed_objects.clear()
        self.updated_packages.clear()
        self.yaml_cache = None
        builtins.getattr = self.original_getattr


registry_manager = IntegrationRegistryManager()


@pytest.fixture(scope="session", autouse=True)
def process_patches_at_end(request):
    """Process all patched objects at the end of the test session."""
    registry_manager.patch_getattr()  # Patch getattr at the start of the session
    
    yield  # Let all tests run
    
    # Runs after all tests have been executed
    registry_manager.process_patched_objects()
    registry_manager.cleanup()