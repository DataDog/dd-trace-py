import json
import pathlib
import sys

from filelock import FileLock
import yaml


class RegistryUpdater:
    """
    Handles loading, merging, and writing integration registry data using file locking.
    """

    def __init__(self):
        self.project_root = self._find_project_root()
        if not self.project_root:
            raise RuntimeError("Could not determine project root directory.")

        self.registry_yaml_path = self.project_root / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml"
        self.registry_lock_path = (
            self.project_root / "ddtrace" / "contrib" / "integration_registry" / "registry.yaml.lock"
        )
        self.lock_timeout_seconds = 15
        self.lock = FileLock(self.registry_lock_path, timeout=self.lock_timeout_seconds)

    def _find_project_root(self) -> pathlib.Path | None:
        """Finds the project root by searching upwards for marker files."""
        current_dir = pathlib.Path(__file__).parent
        for _ in range(5):
            if (current_dir / "pyproject.toml").exists() or (current_dir / ".git").exists():
                return current_dir
            if current_dir.parent == current_dir:
                break
            current_dir = current_dir.parent
        return None

    def load_registry_data(self) -> dict:
        """Safely loads the main registry YAML using a file lock."""
        try:
            self.lock.acquire(timeout=self.lock_timeout_seconds)
            if not self.registry_yaml_path.exists():
                return {"integrations": []}
            with open(self.registry_yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data or {"integrations": []}
        except Exception:
            if self.lock.is_locked:
                self.lock.release()
            return {"integrations": []}

    def load_input_data(self, input_file_path_str: str) -> dict:
        """Loads the JSON data from the specified input file."""
        input_file_path = pathlib.Path(input_file_path_str)
        try:
            with open(input_file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _needs_update(self, registry_data: dict, input_data: dict) -> bool:
        """Checks if input_data contains info not present in registry_data."""
        integrations_list = registry_data.get("integrations", [])
        registry_map = {
            entry.get("integration_name"): entry
            for entry in integrations_list
            if isinstance(entry, dict) and entry.get("integration_name")
        }

        for integration_name, updates in input_data.items():
            new_deps_set = set(updates.get("dependency_name", []))
            if not new_deps_set:
                continue

            if integration_name not in registry_map:
                return True

            entry = registry_map[integration_name]
            if not entry.get("is_external_package"):
                continue

            current_deps_set = set(entry.get("dependency_name", []))
            if not new_deps_set.issubset(current_deps_set):
                return True

        return False

    def merge_data(self, registry_data: dict, input_data: dict) -> bool:
        """Merges dependency info from input_data into registry_data. Assumes check already done."""
        integrations_list = registry_data.setdefault("integrations", [])
        registry_map = {
            entry.get("integration_name"): entry
            for entry in integrations_list
            if isinstance(entry, dict) and entry.get("integration_name")
        }
        changed = False

        for integration_name, updates in input_data.items():
            new_deps_set = set(updates.get("dependency_name", []))
            if not new_deps_set:
                continue

            if integration_name in registry_map:
                entry = registry_map[integration_name]
                if entry.get("is_external_package"):
                    current_deps_set = set(entry.get("dependency_name", []))
                    for dep in new_deps_set:
                        dep_lower = dep.lower()
                        if dep_lower not in current_deps_set:
                            current_deps_set.add(dep_lower)
                            changed = True
                    entry["dependency_name"] = sorted(list(current_deps_set))
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
            with open(self.registry_yaml_path, "w", encoding="utf-8") as f:
                yaml.dump(
                    registry_data,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    indent=2,
                    width=100,
                )
            return True
        except Exception:
            return False
        finally:
            if self.lock.is_locked:
                self.lock.release()

    def run(self, input_file_path_str: str) -> bool:
        """
        Loads data, checks if update needed, merges/writes if necessary.
        Returns True ONLY if changes were successfully written, False otherwise.
        """
        input_data = self.load_input_data(input_file_path_str)
        if not input_data:
            return False

        changes_made = False
        try:
            registry_data = self.load_registry_data()

            if not self._needs_update(registry_data, input_data):
                if self.lock.is_locked:
                    self.lock.release()
                return False

            self.merge_data(registry_data, input_data)

            changes_made = True
            if not self.write_registry_data(registry_data):
                print("RegistryUpdater: Failed to write updated registry data.", file=sys.stderr)
                return False

            return True

        except Exception as e:
            print(f"RegistryUpdater: Error during run: {e}", file=sys.stderr)
            # Ensure lock is released on any exception if still held (e.g., error between load and write)
            if self.lock.is_locked:
                self.lock.release()
            return False
        finally:
            if not changes_made and self.lock.is_locked:
                self.lock.release()
