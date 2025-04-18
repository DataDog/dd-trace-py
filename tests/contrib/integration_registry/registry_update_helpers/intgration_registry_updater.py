import json
import pathlib

import yaml
from filelock import FileLock


class RegistryUpdater:
    """
    Handles loading, merging, and writing integration registry data using file locking.
    """
    def __init__(self):
        self.project_root = self._find_project_root()
        if not self.project_root:
            raise RuntimeError("Could not determine project root directory.")

        self.registry_yaml_path = self.project_root / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml"
        self.registry_lock_path = self.project_root / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml.lock"
        self.lock_timeout_seconds = 15
        self.lock = FileLock(self.registry_lock_path, timeout=self.lock_timeout_seconds)

    def _find_project_root(self) -> pathlib.Path | None:
        """Finds the project root by searching upwards for marker files."""
        current_dir = pathlib.Path(__file__).parent
        for _ in range(5):
            if (current_dir / 'pyproject.toml').exists() or (current_dir / '.git').exists():
                return current_dir
            if current_dir.parent == current_dir:
                break
            current_dir = current_dir.parent
        return None

    def load_registry_data(self) -> dict:
        """Safely loads the main registry YAML using a file lock."""
        try:
            with self.lock.acquire(timeout=self.lock_timeout_seconds):
                if not self.registry_yaml_path.exists():
                    return {"integrations": []}
                with open(self.registry_yaml_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    return data or {"integrations": []}
        except Exception:
            pass
            return {"integrations": []}

    def load_input_data(self, input_file_path_str: str) -> dict:
        """Loads the JSON data from the specified input file."""
        input_file_path = pathlib.Path(input_file_path_str)
        try:
            with open(input_file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def merge_data(self, registry_data: dict, input_data: dict) -> bool:
        """Merges dependency info from input_data into registry_data. Returns True if changed."""
        integrations_list = registry_data.setdefault("integrations", [])
        registry_map = {entry.get("integration_name"): entry for entry in integrations_list if isinstance(entry, dict) and entry.get("integration_name")}
        changed = False

        for integration_name, updates in input_data.items():
            new_deps_set = set(updates.get("dependency_name", []))
            if not new_deps_set: continue

            if integration_name in registry_map:
                entry = registry_map[integration_name]
                if entry.get("is_external_package"):
                    current_deps_set = set(entry.get("dependency_name", []))
                    merged_deps_set = current_deps_set.union(new_deps_set)
                    if merged_deps_set != current_deps_set:
                        entry["dependency_name"] = sorted(list(merged_deps_set))
                        changed = True
            else:
                new_entry = {
                    "integration_name": integration_name,
                    "is_external_package": True,
                    "dependency_name": sorted(list(new_deps_set)),
                }
                integrations_list.append(new_entry)
                changed = True

        if changed:
             registry_data["integrations"] = sorted(integrations_list, key=lambda x: x.get("integration_name", ""))
        return changed

    def write_registry_data(self, registry_data: dict) -> bool:
        """Safely writes the updated data back to registry YAML using a file lock."""
        try:
            with self.lock.acquire(timeout=self.lock_timeout_seconds):
                with open(self.registry_yaml_path, "w", encoding="utf-8") as f:
                     yaml.dump(
                        registry_data, f, default_flow_style=False,
                        sort_keys=False, indent=2, width=100,
                    )
                return True
        except Exception:
            return False

    def run(self, input_file_path_str: str) -> bool:
        """Loads data, merges updates, and writes back to the registry file."""
        input_data = self.load_input_data(input_file_path_str)
        if not input_data:
            return True

        try:
            registry_data = self.load_registry_data()
            changes_made = self.merge_data(registry_data, input_data)

            if changes_made:
                if not self.write_registry_data(registry_data):
                    return False
            else:
                return True
        except Exception:
            return False
